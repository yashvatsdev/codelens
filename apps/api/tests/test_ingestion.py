"""Tests for repository ingestion: tree fetching, file filtering, content
retrieval, and the POST /repositories/{id}/ingest endpoint."""

import base64
import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from app.db.database import SessionLocal
from app.models.repository import Repository
from app.services.github import (
    GitHubAPIError,
    GitHubFileContent,
    GitHubRepoNotFoundError,
    GitHubTreeEntry,
    fetch_file_content,
    fetch_repo_tree,
    is_supported_file,
)
from app.services.ingestion import IngestionResult, ingest_repository


# ---------------------------------------------------------------------------
# Unit tests: is_supported_file
# ---------------------------------------------------------------------------
class TestIsSupportedFile(unittest.TestCase):
    def test_python_file(self):
        self.assertTrue(is_supported_file("app/main.py"))

    def test_typescript_file(self):
        self.assertTrue(is_supported_file("src/index.tsx"))

    def test_json_file(self):
        self.assertTrue(is_supported_file("package.json"))

    def test_dockerfile(self):
        self.assertTrue(is_supported_file("Dockerfile"))

    def test_makefile(self):
        self.assertTrue(is_supported_file("src/Makefile"))

    def test_binary_image(self):
        self.assertFalse(is_supported_file("logo.png"))

    def test_compiled_object(self):
        self.assertFalse(is_supported_file("build/output.o"))

    def test_no_extension(self):
        self.assertFalse(is_supported_file("LICENSE"))

    def test_oversized_file_rejected(self):
        self.assertFalse(is_supported_file("big.py", size=200_000))

    def test_within_size_limit(self):
        self.assertTrue(is_supported_file("small.py", size=50_000))


# ---------------------------------------------------------------------------
# Unit tests: fetch_repo_tree
# ---------------------------------------------------------------------------
class TestFetchRepoTree(unittest.TestCase):
    def _mock_tree_response(self, tree_items: list[dict]):
        data = json.dumps({"tree": tree_items}).encode("utf-8")
        mock = MagicMock()
        mock.read.return_value = data
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    @patch("app.services.github.urllib.request.urlopen")
    def test_fetch_tree_returns_entries(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_tree_response([
            {"path": "README.md", "type": "blob", "sha": "abc123", "size": 100},
            {"path": "src", "type": "tree", "sha": "def456"},
            {"path": "src/main.py", "type": "blob", "sha": "ghi789", "size": 500},
        ])

        entries = fetch_repo_tree("owner", "repo", "main")
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].path, "README.md")
        self.assertEqual(entries[0].type, "blob")
        self.assertEqual(entries[1].type, "tree")
        self.assertEqual(entries[2].path, "src/main.py")

    @patch("app.services.github.urllib.request.urlopen")
    def test_fetch_tree_not_found(self, mock_urlopen):
        err_fp = io.BytesIO(b"{}")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/repos/x/y/git/trees/main?recursive=1",
            code=404, msg="Not Found", hdrs={}, fp=err_fp,
        )
        with self.assertRaises(GitHubRepoNotFoundError):
            fetch_repo_tree("x", "y", "main")
        err_fp.close()

    @patch("app.services.github.urllib.request.urlopen")
    def test_fetch_tree_api_error(self, mock_urlopen):
        err_fp = io.BytesIO(b"{}")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/repos/x/y/git/trees/main?recursive=1",
            code=500, msg="Internal Server Error", hdrs={}, fp=err_fp,
        )
        with self.assertRaises(GitHubAPIError):
            fetch_repo_tree("x", "y", "main")
        err_fp.close()


# ---------------------------------------------------------------------------
# Unit tests: fetch_file_content
# ---------------------------------------------------------------------------
class TestFetchFileContent(unittest.TestCase):
    def _mock_content_response(self, path: str, content_text: str, sha: str = "abc"):
        encoded = base64.b64encode(content_text.encode("utf-8")).decode("ascii")
        data = json.dumps({
            "path": path,
            "sha": sha,
            "size": len(content_text),
            "content": encoded,
            "encoding": "base64",
        }).encode("utf-8")
        mock = MagicMock()
        mock.read.return_value = data
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    @patch("app.services.github.urllib.request.urlopen")
    def test_fetch_file_content_success(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_content_response(
            "src/main.py", "print('hello')", sha="sha123",
        )
        result = fetch_file_content("owner", "repo", "src/main.py")
        self.assertEqual(result.path, "src/main.py")
        self.assertEqual(result.content, "print('hello')")
        self.assertEqual(result.sha, "sha123")

    @patch("app.services.github.urllib.request.urlopen")
    def test_fetch_file_content_not_found(self, mock_urlopen):
        err_fp = io.BytesIO(b"{}")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/repos/x/y/contents/missing.py",
            code=404, msg="Not Found", hdrs={}, fp=err_fp,
        )
        with self.assertRaises(GitHubRepoNotFoundError):
            fetch_file_content("x", "y", "missing.py")
        err_fp.close()


# ---------------------------------------------------------------------------
# Integration tests: ingest_repository service
# ---------------------------------------------------------------------------
class TestIngestRepository(unittest.TestCase):
    @patch("app.services.ingestion.fetch_file_content")
    @patch("app.services.ingestion.fetch_repo_tree")
    def test_ingest_filters_and_fetches(self, mock_tree, mock_content):
        mock_tree.return_value = [
            GitHubTreeEntry(path="README.md", type="blob", sha="a1", size=50),
            GitHubTreeEntry(path="src", type="tree", sha="d1"),
            GitHubTreeEntry(path="src/app.py", type="blob", sha="a2", size=200),
            GitHubTreeEntry(path="src/utils.ts", type="blob", sha="a3", size=300),
            GitHubTreeEntry(path="logo.png", type="blob", sha="a4", size=5000),
            GitHubTreeEntry(path="build/output.o", type="blob", sha="a5", size=1000),
        ]
        mock_content.side_effect = [
            GitHubFileContent(path="README.md", sha="a1", content="# Readme", size=50),
            GitHubFileContent(path="src/app.py", sha="a2", content="print(1)", size=200),
            GitHubFileContent(path="src/utils.ts", sha="a3", content="export {}", size=300),
        ]

        result = ingest_repository("owner", "repo", "main")

        self.assertEqual(result.total_tree_entries, 6)
        # README.md (.md), src/app.py (.py), src/utils.ts (.ts) are supported
        # src (dir), logo.png, build/output.o are not
        self.assertEqual(result.files_identified, 3)
        self.assertEqual(result.files_fetched, 3)
        self.assertEqual(len(result.fetched_files), 3)
        self.assertEqual(result.errors, [])

    @patch("app.services.ingestion.fetch_file_content")
    @patch("app.services.ingestion.fetch_repo_tree")
    def test_ingest_records_fetch_errors(self, mock_tree, mock_content):
        mock_tree.return_value = [
            GitHubTreeEntry(path="main.py", type="blob", sha="a1", size=100),
        ]
        mock_content.side_effect = GitHubAPIError("timeout")

        result = ingest_repository("owner", "repo", "main")

        self.assertEqual(result.files_identified, 1)
        self.assertEqual(result.files_fetched, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("main.py", result.errors[0])

    @patch("app.services.ingestion.fetch_repo_tree")
    def test_ingest_tree_not_found_raises(self, mock_tree):
        mock_tree.side_effect = GitHubRepoNotFoundError("not found")
        with self.assertRaises(GitHubRepoNotFoundError):
            ingest_repository("owner", "nonexistent", "main")

    @patch("app.services.ingestion.fetch_file_content")
    @patch("app.services.ingestion.fetch_repo_tree")
    def test_ingest_respects_max_files(self, mock_tree, mock_content):
        # Create 5 tree entries but set max_files=2
        mock_tree.return_value = [
            GitHubTreeEntry(path=f"file{i}.py", type="blob", sha=f"s{i}", size=100)
            for i in range(5)
        ]
        mock_content.side_effect = [
            GitHubFileContent(path=f"file{i}.py", sha=f"s{i}", content="x", size=100)
            for i in range(2)
        ]

        result = ingest_repository("owner", "repo", "main", max_files=2)

        self.assertEqual(result.files_identified, 5)
        self.assertEqual(result.files_fetched, 2)
        self.assertEqual(result.files_skipped, 3)


# ---------------------------------------------------------------------------
# Endpoint tests: POST /repositories/{id}/ingest
# ---------------------------------------------------------------------------
class TestIngestEndpoint(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        test_repos = self.db.query(Repository).filter(
            Repository.owner == "ingest-test-org"
        ).all()
        for repo in test_repos:
            self.db.delete(repo)
        self.db.commit()

    def _create_repo(self) -> Repository:
        repo = Repository(
            github_id="ingest-test-org/test-repo",
            name="test-repo",
            full_name="ingest-test-org/test-repo",
            owner="ingest-test-org",
            url="https://github.com/ingest-test-org/test-repo",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    @patch("app.api.routes.repositories.ingest_repository")
    def test_ingest_endpoint_success(self, mock_ingest):
        from app.api.routes.repositories import ingest_repository_endpoint
        from app.services.ingestion import IngestionFileEntry

        repo = self._create_repo()
        mock_ingest.return_value = IngestionResult(
            owner="ingest-test-org",
            repo="test-repo",
            branch="main",
            total_tree_entries=10,
            files_identified=3,
            files_fetched=3,
            files_skipped=0,
            fetched_files=[
                IngestionFileEntry(path="main.py", sha="abc", size=100),
            ],
            errors=[],
        )

        result = ingest_repository_endpoint(repo.id, db=self.db)

        self.assertEqual(result.files_fetched, 3)
        self.assertEqual(result.owner, "ingest-test-org")
        mock_ingest.assert_called_once_with(
            owner="ingest-test-org",
            repo="test-repo",
            branch="main",
        )

    def test_ingest_endpoint_repo_not_found(self):
        from fastapi import HTTPException
        from app.api.routes.repositories import ingest_repository_endpoint

        with self.assertRaises(HTTPException) as ctx:
            ingest_repository_endpoint(999999, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("app.api.routes.repositories.ingest_repository")
    def test_ingest_endpoint_github_error(self, mock_ingest):
        from fastapi import HTTPException
        from app.api.routes.repositories import ingest_repository_endpoint

        repo = self._create_repo()
        mock_ingest.side_effect = GitHubRepoNotFoundError("repo not found on GitHub")

        with self.assertRaises(HTTPException) as ctx:
            ingest_repository_endpoint(repo.id, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
