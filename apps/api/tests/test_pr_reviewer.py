"""Tests for the Pull Request review feature.

Tests the PR reviewer service, the API endpoint, and verifies that
no database records (Finding, SourceFile) are created.
"""

import base64
import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.db.database import SessionLocal
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.services.github import (
    GitHubAPIError,
    GitHubRateLimitError,
)
from app.services.pr_reviewer import (
    PRChangedFile,
    PRNotFoundError,
    PRReviewResult,
    review_pull_request,
    _is_analyzable_extension,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_github_json_response(data):
    """Create a mock urllib response returning JSON data."""
    response_bytes = json.dumps(data).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_bytes
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _base64_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _make_pr_file(
    filename: str,
    status: str = "modified",
    patch: str | None = None,
    contents_url: str | None = None,
    previous_filename: str | None = None,
) -> dict:
    """Create a dict mimicking a GitHub PR file entry."""
    entry = {
        "filename": filename,
        "status": status,
        "patch": patch or "",
    }
    if contents_url is not None:
        entry["contents_url"] = contents_url
    else:
        entry["contents_url"] = f"https://api.github.com/repos/owner/repo/contents/{filename}?ref=pr-sha"
    if previous_filename:
        entry["previous_filename"] = previous_filename
    return entry


# ---------------------------------------------------------------------------
# Unit tests: _is_analyzable_extension
# ---------------------------------------------------------------------------
class TestIsAnalyzableExtension(unittest.TestCase):
    def test_python_file(self):
        self.assertTrue(_is_analyzable_extension("src/app.py"))

    def test_typescript_file(self):
        self.assertTrue(_is_analyzable_extension("src/index.ts"))

    def test_tsx_file(self):
        self.assertTrue(_is_analyzable_extension("src/App.tsx"))

    def test_javascript_file(self):
        self.assertTrue(_is_analyzable_extension("lib/utils.js"))

    def test_jsx_file(self):
        self.assertTrue(_is_analyzable_extension("src/Component.jsx"))

    def test_mjs_file(self):
        self.assertTrue(_is_analyzable_extension("lib/module.mjs"))

    def test_cjs_file(self):
        self.assertTrue(_is_analyzable_extension("lib/module.cjs"))

    def test_mts_file(self):
        self.assertTrue(_is_analyzable_extension("lib/module.mts"))

    def test_cts_file(self):
        self.assertTrue(_is_analyzable_extension("lib/module.cts"))

    def test_markdown_not_analyzable(self):
        self.assertFalse(_is_analyzable_extension("README.md"))

    def test_json_not_analyzable(self):
        self.assertFalse(_is_analyzable_extension("package.json"))

    def test_image_not_analyzable(self):
        self.assertFalse(_is_analyzable_extension("logo.png"))

    def test_yaml_not_analyzable(self):
        self.assertFalse(_is_analyzable_extension("config.yaml"))


# ---------------------------------------------------------------------------
# Unit tests: review_pull_request service
# ---------------------------------------------------------------------------
class TestReviewPullRequest(unittest.TestCase):
    """Test the core PR review logic with all GitHub API calls mocked."""

    @patch("app.services.pr_reviewer._github_api_request")
    def test_successful_review_python_file(self, mock_api):
        """Python file with a finding is analyzed and returned."""
        python_content = "import os\n\nx = 1\n"

        mock_api.side_effect = [
            # 1. PR metadata
            {"number": 1, "state": "open"},
            # 2. PR files
            [_make_pr_file("src/app.py", status="modified")],
            # 3. File content
            {"path": "src/app.py", "sha": "abc", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 1, repository_id=100)

        self.assertEqual(result.repository_id, 100)
        self.assertEqual(result.pull_request_number, 1)
        self.assertEqual(result.files_changed, 1)
        self.assertEqual(result.files_analyzed, 1)
        # "import os" is unused → PY-UNUSED-IMPORT
        self.assertTrue(len(result.findings) >= 1)
        rule_ids = [f.rule_id for f in result.findings]
        self.assertIn("PY-UNUSED-IMPORT", rule_ids)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_successful_review_js_file(self, mock_api):
        """JavaScript file with console.log is detected."""
        js_content = "console.log('hello');\n"

        mock_api.side_effect = [
            {"number": 2, "state": "open"},
            [_make_pr_file("src/index.js", status="added")],
            {"path": "src/index.js", "sha": "def", "size": len(js_content),
             "content": _base64_encode(js_content)},
        ]

        result = review_pull_request("owner", "repo", 2, repository_id=100)

        self.assertEqual(result.files_analyzed, 1)
        rule_ids = [f.rule_id for f in result.findings]
        self.assertIn("JS-CONSOLE-LOG", rule_ids)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_successful_review_ts_file(self, mock_api):
        """TypeScript file with eval() is detected."""
        ts_content = "const x = eval('1+1');\n"

        mock_api.side_effect = [
            {"number": 3, "state": "open"},
            [_make_pr_file("src/utils.ts", status="modified")],
            {"path": "src/utils.ts", "sha": "ghi", "size": len(ts_content),
             "content": _base64_encode(ts_content)},
        ]

        result = review_pull_request("owner", "repo", 3, repository_id=100)

        self.assertEqual(result.files_analyzed, 1)
        rule_ids = [f.rule_id for f in result.findings]
        self.assertIn("JS-EVAL-USAGE", rule_ids)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_mixed_python_and_js_files(self, mock_api):
        """PR with both Python and JS/TS files analyzes all."""
        py_content = "import sys\nx = 1\n"
        js_content = "debugger;\n"

        mock_api.side_effect = [
            {"number": 4, "state": "open"},
            [
                _make_pr_file("src/main.py", status="added"),
                _make_pr_file("src/app.tsx", status="modified"),
            ],
            {"path": "src/main.py", "sha": "a1", "size": len(py_content),
             "content": _base64_encode(py_content)},
            {"path": "src/app.tsx", "sha": "a2", "size": len(js_content),
             "content": _base64_encode(js_content)},
        ]

        result = review_pull_request("owner", "repo", 4, repository_id=100)

        self.assertEqual(result.files_changed, 2)
        self.assertEqual(result.files_analyzed, 2)
        rule_ids = [f.rule_id for f in result.findings]
        self.assertIn("PY-UNUSED-IMPORT", rule_ids)
        self.assertIn("JS-DEBUGGER", rule_ids)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_unsupported_files_skipped(self, mock_api):
        """Non-analyzable files (markdown, images, etc.) are skipped."""
        mock_api.side_effect = [
            {"number": 5, "state": "open"},
            [
                _make_pr_file("README.md", status="modified"),
                _make_pr_file("docs/image.png", status="added"),
                _make_pr_file("package.json", status="modified"),
            ],
        ]

        result = review_pull_request("owner", "repo", 5, repository_id=100)

        self.assertEqual(result.files_changed, 3)
        self.assertEqual(result.files_analyzed, 0)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(len(result.skipped_files), 3)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_removed_files_skipped(self, mock_api):
        """Removed files are not analyzed (content no longer exists)."""
        mock_api.side_effect = [
            {"number": 6, "state": "open"},
            [_make_pr_file("src/old_module.py", status="removed")],
        ]

        result = review_pull_request("owner", "repo", 6, repository_id=100)

        self.assertEqual(result.files_changed, 1)
        self.assertEqual(result.files_analyzed, 0)
        self.assertIn("src/old_module.py (removed)", result.skipped_files)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_added_files_analyzed(self, mock_api):
        """Added files are analyzed when they have a supported extension."""
        content = "x = 1\n"

        mock_api.side_effect = [
            {"number": 7, "state": "open"},
            [_make_pr_file("new_feature.py", status="added")],
            {"path": "new_feature.py", "sha": "x1", "size": len(content),
             "content": _base64_encode(content)},
        ]

        result = review_pull_request("owner", "repo", 7, repository_id=100)

        self.assertEqual(result.files_analyzed, 1)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_modified_files_analyzed(self, mock_api):
        """Modified files are analyzed when they have a supported extension."""
        content = "print('hello')\n"

        mock_api.side_effect = [
            {"number": 8, "state": "open"},
            [_make_pr_file("src/utils.py", status="modified")],
            {"path": "src/utils.py", "sha": "m1", "size": len(content),
             "content": _base64_encode(content)},
        ]

        result = review_pull_request("owner", "repo", 8, repository_id=100)

        self.assertEqual(result.files_analyzed, 1)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_renamed_files_analyzed(self, mock_api):
        """Renamed files are analyzed using the new filename."""
        content = "x = 42\n"

        mock_api.side_effect = [
            {"number": 9, "state": "open"},
            [_make_pr_file(
                "src/new_name.py",
                status="renamed",
                previous_filename="src/old_name.py",
            )],
            {"path": "src/new_name.py", "sha": "r1", "size": len(content),
             "content": _base64_encode(content)},
        ]

        result = review_pull_request("owner", "repo", 9, repository_id=100)

        self.assertEqual(result.files_analyzed, 1)
        # Findings should reference the new filename
        if result.findings:
            self.assertEqual(result.findings[0].file_path, "src/new_name.py")

    @patch("app.services.pr_reviewer._github_api_request")
    def test_pr_not_found(self, mock_api):
        """PRNotFoundError is raised when the PR doesn't exist."""
        mock_api.side_effect = PRNotFoundError("PR not found")

        with self.assertRaises(PRNotFoundError):
            review_pull_request("owner", "repo", 999, repository_id=100)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_github_api_failure(self, mock_api):
        """GitHubAPIError is raised on upstream failure."""
        mock_api.side_effect = GitHubAPIError("GitHub API returned error 500: Internal Server Error")

        with self.assertRaises(GitHubAPIError):
            review_pull_request("owner", "repo", 1, repository_id=100)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_correct_findings_returned(self, mock_api):
        """Verify the exact shape and content of returned findings."""
        py_content = "try:\n    pass\nexcept:\n    pass\n"

        mock_api.side_effect = [
            {"number": 10, "state": "open"},
            [_make_pr_file("src/handler.py", status="modified")],
            {"path": "src/handler.py", "sha": "f1", "size": len(py_content),
             "content": _base64_encode(py_content)},
        ]

        result = review_pull_request("owner", "repo", 10, repository_id=100)

        self.assertEqual(result.files_analyzed, 1)
        bare_except_findings = [
            f for f in result.findings if f.rule_id == "PY-BARE-EXCEPT"
        ]
        self.assertEqual(len(bare_except_findings), 1)
        finding = bare_except_findings[0]
        self.assertEqual(finding.file_path, "src/handler.py")
        self.assertEqual(finding.severity, "warning")
        self.assertEqual(finding.category, "bug")
        self.assertEqual(finding.line_number, 3)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_no_analyzable_files_returns_empty_findings(self, mock_api):
        """PR with only non-analyzable files returns zero findings."""
        mock_api.side_effect = [
            {"number": 11, "state": "open"},
            [
                _make_pr_file("Makefile", status="modified"),
                _make_pr_file(".gitignore", status="modified"),
            ],
        ]

        result = review_pull_request("owner", "repo", 11, repository_id=100)

        self.assertEqual(result.files_analyzed, 0)
        self.assertEqual(len(result.findings), 0)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_file_fetch_error_is_recorded(self, mock_api):
        """A file content fetch failure is recorded in errors, not raised."""
        mock_api.side_effect = [
            {"number": 12, "state": "open"},
            [_make_pr_file("src/app.py", status="modified")],
            GitHubAPIError("timeout fetching file"),
        ]

        result = review_pull_request("owner", "repo", 12, repository_id=100)

        self.assertEqual(result.files_analyzed, 0)
        self.assertTrue(len(result.errors) >= 1)
        self.assertIn("src/app.py", result.errors[0])

    @patch("app.services.pr_reviewer._github_api_request")
    def test_pagination_handles_multiple_pages(self, mock_api):
        """Verify pagination works when PR has more files than one page."""
        py_content = "x = 1\n"
        # Page 1: 100 files (triggers next page), Page 2: 5 files
        page1_files = [
            _make_pr_file(f"file_{i}.py", status="added")
            for i in range(100)
        ]
        page2_files = [
            _make_pr_file(f"file_{100+i}.py", status="added")
            for i in range(5)
        ]
        content_response = {
            "path": "dummy.py", "sha": "x", "size": len(py_content),
            "content": _base64_encode(py_content),
        }

        responses = [
            {"number": 13, "state": "open"},  # PR metadata
            page1_files,                        # page 1
            page2_files,                        # page 2
        ]
        # Add content responses for all 105 files
        responses.extend([content_response] * 105)
        mock_api.side_effect = responses

        result = review_pull_request("owner", "repo", 13, repository_id=100)

        self.assertEqual(result.files_changed, 105)
        self.assertEqual(result.files_analyzed, 105)


# ---------------------------------------------------------------------------
# Database safety tests: no records created
# ---------------------------------------------------------------------------
class TestPRReviewNoDatabaseWrites(unittest.TestCase):
    """Verify that the PR review service does NOT create Finding or SourceFile records."""

    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "pr-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "pr-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "pr-test-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self) -> Repository:
        repo = Repository(
            github_id="pr-test-org/pr-repo",
            name="pr-repo",
            full_name="pr-test-org/pr-repo",
            owner="pr-test-org",
            url="https://github.com/pr-test-org/pr-repo",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    @patch("app.services.pr_reviewer._github_api_request")
    def test_no_finding_records_created(self, mock_api):
        """PR review must not create Finding rows in the database."""
        repo = self._create_repo()
        py_content = "import os\nx = 1\n"

        mock_api.side_effect = [
            {"number": 1, "state": "open"},
            [_make_pr_file("app.py", status="modified")],
            {"path": "app.py", "sha": "abc", "size": len(py_content),
             "content": _base64_encode(py_content)},
        ]

        result = review_pull_request("pr-test-org", "pr-repo", 1, repository_id=repo.id)

        # Service returns findings in memory
        self.assertTrue(len(result.findings) >= 1)

        # But NO Finding records exist in the database
        db_findings = self.db.query(Finding).filter(
            Finding.repository_id == repo.id
        ).count()
        self.assertEqual(db_findings, 0)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_no_source_file_records_modified(self, mock_api):
        """PR review must not create or modify SourceFile rows."""
        repo = self._create_repo()
        py_content = "print('hello')\n"

        mock_api.side_effect = [
            {"number": 2, "state": "open"},
            [_make_pr_file("main.py", status="added")],
            {"path": "main.py", "sha": "def", "size": len(py_content),
             "content": _base64_encode(py_content)},
        ]

        before_count = self.db.query(SourceFile).filter(
            SourceFile.repository_id == repo.id
        ).count()

        review_pull_request("pr-test-org", "pr-repo", 2, repository_id=repo.id)

        after_count = self.db.query(SourceFile).filter(
            SourceFile.repository_id == repo.id
        ).count()
        self.assertEqual(before_count, after_count)


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------
class TestPRReviewEndpoint(unittest.TestCase):
    """Test the POST /repositories/{id}/pull-requests/{pr}/review endpoint."""

    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Repository).filter(
            Repository.owner == "pr-endpoint-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self) -> Repository:
        repo = Repository(
            github_id="pr-endpoint-org/test-repo",
            name="test-repo",
            full_name="pr-endpoint-org/test-repo",
            owner="pr-endpoint-org",
            url="https://github.com/pr-endpoint-org/test-repo",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    def test_repository_not_found_returns_404(self):
        from app.api.routes.repositories import review_pull_request_endpoint

        with self.assertRaises(HTTPException) as ctx:
            review_pull_request_endpoint(999999, 1, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("not found", ctx.exception.detail.lower())

    @patch("app.api.routes.repositories.review_pull_request")
    def test_pr_not_found_returns_404(self, mock_review):
        from app.api.routes.repositories import review_pull_request_endpoint

        repo = self._create_repo()
        mock_review.side_effect = PRNotFoundError("PR 999 not found")

        with self.assertRaises(HTTPException) as ctx:
            review_pull_request_endpoint(repo.id, 999, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("999", ctx.exception.detail)

    @patch("app.api.routes.repositories.review_pull_request")
    def test_github_api_error_returns_502(self, mock_review):
        from app.api.routes.repositories import review_pull_request_endpoint

        repo = self._create_repo()
        mock_review.side_effect = GitHubAPIError("upstream failure")

        with self.assertRaises(HTTPException) as ctx:
            review_pull_request_endpoint(repo.id, 1, db=self.db)
        self.assertEqual(ctx.exception.status_code, 502)

    @patch("app.api.routes.repositories.review_pull_request")
    def test_rate_limit_returns_429(self, mock_review):
        from app.api.routes.repositories import review_pull_request_endpoint

        repo = self._create_repo()
        mock_review.side_effect = GitHubRateLimitError("rate limit exceeded")

        with self.assertRaises(HTTPException) as ctx:
            review_pull_request_endpoint(repo.id, 1, db=self.db)
        self.assertEqual(ctx.exception.status_code, 429)

    @patch("app.api.routes.repositories.review_pull_request")
    def test_successful_review_endpoint(self, mock_review):
        from app.api.routes.repositories import review_pull_request_endpoint

        repo = self._create_repo()
        mock_review.return_value = PRReviewResult(
            repository_id=repo.id,
            pull_request_number=12,
            files_changed=5,
            files_analyzed=4,
            findings=[],
        )

        result = review_pull_request_endpoint(repo.id, 12, db=self.db)

        self.assertEqual(result.repository_id, repo.id)
        self.assertEqual(result.pull_request_number, 12)
        self.assertEqual(result.files_changed, 5)
        self.assertEqual(result.files_analyzed, 4)
        mock_review.assert_called_once_with(
            owner="pr-endpoint-org",
            repo="test-repo",
            pr_number=12,
            repository_id=repo.id,
        )


if __name__ == "__main__":
    unittest.main()
