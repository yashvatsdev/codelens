import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.api.routes.repositories import (
    connect_github_repository,
    create_repository,
    delete_repository,
    get_github_repository_metadata,
    get_github_repository_metadata_query,
    get_repositories,
    get_repository,
)
from app.db.database import SessionLocal
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.schemas.repository import (
    GitHubMetadataRequest,
    GitHubRepositoryCreate,
    RepositoryCreate,
)
from app.services.github import (
    GitHubAPIError,
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
    fetch_github_metadata,
    parse_github_url,
)


class TestGitHubUrlParser(unittest.TestCase):
    def test_valid_https_url(self):
        parsed = parse_github_url("https://github.com/yashvatsdev/codelens")
        self.assertEqual(parsed.owner, "yashvatsdev")
        self.assertEqual(parsed.name, "codelens")
        self.assertEqual(parsed.full_name, "yashvatsdev/codelens")
        self.assertEqual(parsed.url, "https://github.com/yashvatsdev/codelens")

    def test_valid_https_with_git_suffix(self):
        parsed = parse_github_url("https://github.com/facebook/react.git")
        self.assertEqual(parsed.owner, "facebook")
        self.assertEqual(parsed.name, "react")
        self.assertEqual(parsed.full_name, "facebook/react")
        self.assertEqual(parsed.url, "https://github.com/facebook/react")

    def test_valid_https_with_trailing_slash(self):
        parsed = parse_github_url("https://github.com/torvalds/linux/")
        self.assertEqual(parsed.owner, "torvalds")
        self.assertEqual(parsed.name, "linux")
        self.assertEqual(parsed.full_name, "torvalds/linux")

    def test_valid_http_and_www(self):
        parsed = parse_github_url("http://www.github.com/psf/requests")
        self.assertEqual(parsed.owner, "psf")
        self.assertEqual(parsed.name, "requests")
        self.assertEqual(parsed.url, "https://github.com/psf/requests")

    def test_valid_ssh_format(self):
        parsed = parse_github_url("git@github.com:pallets/flask.git")
        self.assertEqual(parsed.owner, "pallets")
        self.assertEqual(parsed.name, "flask")
        self.assertEqual(parsed.full_name, "pallets/flask")

    def test_valid_schemeless(self):
        parsed = parse_github_url("github.com/encode/uvicorn")
        self.assertEqual(parsed.owner, "encode")
        self.assertEqual(parsed.name, "uvicorn")

    def test_invalid_empty_url(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("")
        self.assertIn("cannot be empty", str(ctx.exception).lower())

    def test_invalid_domain(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("https://gitlab.com/owner/repo")
        self.assertIn("only github repository urls", str(ctx.exception).lower())

    def test_invalid_missing_repo(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("https://github.com/owner")
        self.assertIn("must contain both owner and repository name", str(ctx.exception).lower())

    def test_invalid_deep_path(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("https://github.com/owner/repo/tree/main")
        self.assertIn("must contain both owner and repository name", str(ctx.exception).lower())


class TestGitHubMetadataFetching(unittest.TestCase):
    def _mock_response(self, data: dict):
        response_bytes = json.dumps(data).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_bytes
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    @patch("urllib.request.urlopen")
    def test_fetch_github_metadata_success(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "name": "fastapi",
            "full_name": "fastapi/fastapi",
            "owner": {"login": "fastapi"},
            "description": "FastAPI framework, high performance, easy to learn.",
            "default_branch": "master",
            "html_url": "https://github.com/fastapi/fastapi",
        })

        meta = fetch_github_metadata("https://github.com/fastapi/fastapi.git")
        self.assertEqual(meta.owner, "fastapi")
        self.assertEqual(meta.name, "fastapi")
        self.assertEqual(meta.full_name, "fastapi/fastapi")
        self.assertEqual(meta.description, "FastAPI framework, high performance, easy to learn.")
        self.assertEqual(meta.default_branch, "master")
        self.assertEqual(meta.url, "https://github.com/fastapi/fastapi")

    def test_fetch_github_metadata_invalid_url(self):
        with self.assertRaises(ValueError) as ctx:
            fetch_github_metadata("https://bitbucket.org/owner/repo")
        self.assertIn("only github repository urls", str(ctx.exception).lower())

    @patch("urllib.request.urlopen")
    def test_fetch_github_metadata_not_found(self, mock_urlopen):
        err_fp = io.BytesIO(b"{}")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/repos/owner/nonexistent",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=err_fp,
        )
        with self.assertRaises(GitHubRepoNotFoundError) as ctx:
            fetch_github_metadata("https://github.com/owner/nonexistent")
        err_fp.close()
        self.assertIn("not found or is private", str(ctx.exception).lower())

    @patch("urllib.request.urlopen")
    def test_fetch_github_metadata_rate_limit(self, mock_urlopen):
        err_fp = io.BytesIO(b"{}")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/repos/owner/repo",
            code=403,
            msg="rate limit exceeded",
            hdrs={},
            fp=err_fp,
        )
        with self.assertRaises(GitHubRateLimitError) as ctx:
            fetch_github_metadata("https://github.com/owner/repo")
        err_fp.close()
        self.assertIn("rate limit exceeded", str(ctx.exception).lower())

    @patch("urllib.request.urlopen")
    def test_fetch_github_metadata_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(GitHubAPIError) as ctx:
            fetch_github_metadata("https://github.com/owner/repo")
        self.assertIn("failed to reach github api", str(ctx.exception).lower())


class TestGitHubMetadataEndpoint(unittest.TestCase):
    def _mock_response(self, data: dict):
        response_bytes = json.dumps(data).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_bytes
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    @patch("urllib.request.urlopen")
    def test_post_metadata_success(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "name": "codelens",
            "full_name": "yashvatsdev/codelens",
            "owner": {"login": "yashvatsdev"},
            "description": "AI-Powered Code Intelligence Platform",
            "default_branch": "main",
            "html_url": "https://github.com/yashvatsdev/codelens",
        })

        payload = GitHubMetadataRequest(url="https://github.com/yashvatsdev/codelens")
        result = get_github_repository_metadata(payload)

        self.assertEqual(result.owner, "yashvatsdev")
        self.assertEqual(result.name, "codelens")
        self.assertEqual(result.description, "AI-Powered Code Intelligence Platform")
        self.assertEqual(result.default_branch, "main")
        self.assertEqual(result.url, "https://github.com/yashvatsdev/codelens")

    def test_post_metadata_invalid_url_returns_400(self):
        payload = GitHubMetadataRequest(url="https://not-github.org/bad/url")
        with self.assertRaises(HTTPException) as ctx:
            get_github_repository_metadata(payload)
        self.assertEqual(ctx.exception.status_code, 400)

    @patch("urllib.request.urlopen")
    def test_post_metadata_not_found_returns_404(self, mock_urlopen):
        err_fp = io.BytesIO(b"{}")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/repos/unknown/project",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=err_fp,
        )
        payload = GitHubMetadataRequest(url="https://github.com/unknown/project")
        with self.assertRaises(HTTPException) as ctx:
            get_github_repository_metadata(payload)
        err_fp.close()
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("urllib.request.urlopen")
    def test_get_metadata_query_success(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response({
            "name": "react",
            "full_name": "facebook/react",
            "owner": {"login": "facebook"},
            "description": "The library for web and native user interfaces.",
            "default_branch": "main",
            "html_url": "https://github.com/facebook/react",
        })

        result = get_github_repository_metadata_query("https://github.com/facebook/react")
        self.assertEqual(result.name, "react")
        self.assertEqual(result.owner, "facebook")


class TestRepositoryDatabaseEndpoints(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        test_repos = self.db.query(Repository).filter(
            Repository.owner.in_(["test-org", "test-url-org"])
        ).all()
        for repo in test_repos:
            self.db.delete(repo)
        self.db.commit()

    def test_connect_github_repository_success(self):
        payload = GitHubRepositoryCreate(
            url="https://github.com/test-org/test-project",
            default_branch="develop",
        )
        repo = connect_github_repository(payload, self.db)
        self.assertIsNotNone(repo.id)
        self.assertEqual(repo.name, "test-project")
        self.assertEqual(repo.owner, "test-org")
        self.assertEqual(repo.full_name, "test-org/test-project")
        self.assertEqual(repo.url, "https://github.com/test-org/test-project")
        self.assertEqual(repo.default_branch, "develop")

    def test_connect_github_repository_duplicate_raises_409(self):
        payload = GitHubRepositoryCreate(url="https://github.com/test-org/duplicate-project")
        connect_github_repository(payload, self.db)

        with self.assertRaises(HTTPException) as ctx:
            connect_github_repository(payload, self.db)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_connect_github_repository_invalid_url_raises_400(self):
        payload = GitHubRepositoryCreate(url="https://notgithub.com/test-org/proj")
        with self.assertRaises(HTTPException) as ctx:
            connect_github_repository(payload, self.db)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_repository_with_url_auto_parse(self):
        payload = RepositoryCreate(url="https://github.com/test-url-org/auto-parsed.git")
        repo = create_repository(payload, self.db)
        self.assertEqual(repo.name, "auto-parsed")
        self.assertEqual(repo.owner, "test-url-org")
        self.assertEqual(repo.full_name, "test-url-org/auto-parsed")

    def test_delete_repository_not_found(self):
        with self.assertRaises(HTTPException) as ctx:
            delete_repository(999999, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_delete_repository_success_with_cascade(self):
        payload = GitHubRepositoryCreate(
            url="https://github.com/test-org/delete-cascade-repo",
            default_branch="main",
        )
        repo = connect_github_repository(payload, self.db)
        repo_id = repo.id

        # Add related SourceFile and Finding
        sf = SourceFile(
            repository_id=repo_id,
            path="src/index.js",
            sha="sha_del",
            content="console.log('del');",
            size=19,
        )
        finding = Finding(
            repository_id=repo_id,
            file_path="src/index.js",
            line_number=1,
            severity="warning",
            category="style",
            message="Unexpected console.log",
            rule_id="JS-CONSOLE-LOG",
        )
        self.db.add_all([sf, finding])
        self.db.commit()

        # Verify records exist before delete
        self.assertEqual(self.db.query(SourceFile).filter(SourceFile.repository_id == repo_id).count(), 1)
        self.assertEqual(self.db.query(Finding).filter(Finding.repository_id == repo_id).count(), 1)

        # Execute delete
        res = delete_repository(repo_id, db=self.db)
        self.assertEqual(res["status"], "ok")

        # Verify repository and cascading records are gone
        self.assertIsNone(self.db.get(Repository, repo_id))
        self.assertEqual(self.db.query(SourceFile).filter(SourceFile.repository_id == repo_id).count(), 0)
        self.assertEqual(self.db.query(Finding).filter(Finding.repository_id == repo_id).count(), 0)


if __name__ == "__main__":
    unittest.main()
