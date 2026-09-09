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
    _is_analyzable_extension,
    extract_changed_line_ranges,
    is_line_changed,
    review_pull_request,
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
        "patch": patch,
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


# ---------------------------------------------------------------------------
# Phase 4: Diff-Aware PR Review Tests
# ---------------------------------------------------------------------------
class TestDiffAwarePRReviewer(unittest.TestCase):
    """Test diff parser helpers and diff-aware finding filtering."""

    def test_extract_single_changed_hunk(self):
        patch = "@@ -10,4 +10,8 @@ def foo():\n+new_line\n"
        ranges = extract_changed_line_ranges(patch)
        self.assertEqual(ranges, [(10, 17)])

    def test_extract_multiple_changed_hunks(self):
        patch = (
            "@@ -1,2 +1,3 @@\n+line1\n"
            "@@ -20,5 +21,4 @@\n+line2\n"
        )
        ranges = extract_changed_line_ranges(patch)
        self.assertEqual(ranges, [(1, 3), (21, 24)])

    def test_extract_deleted_lines_not_treated_as_changed(self):
        # count=0 indicates pure deletion in new file; should be ignored
        patch = "@@ -10,4 +10,0 @@\n-deleted_line\n"
        ranges = extract_changed_line_ranges(patch)
        self.assertEqual(ranges, [])

    def test_extract_empty_patch(self):
        self.assertEqual(extract_changed_line_ranges(""), [])

    def test_extract_missing_patch(self):
        self.assertEqual(extract_changed_line_ranges(None), [])

    def test_extract_malformed_patch(self):
        malformed = "some git log header without unified diff @@ broken @@"
        self.assertEqual(extract_changed_line_ranges(malformed), [])

    def test_extract_default_count_one(self):
        # When count is omitted (+5), count defaults to 1
        patch = "@@ -5 +5 @@\n+single line"
        ranges = extract_changed_line_ranges(patch)
        self.assertEqual(ranges, [(5, 5)])

    def test_is_line_changed_inside_range(self):
        ranges = [(10, 17)]
        self.assertTrue(is_line_changed(10, ranges))
        self.assertTrue(is_line_changed(14, ranges))
        self.assertTrue(is_line_changed(17, ranges))

    def test_is_line_changed_outside_range(self):
        ranges = [(10, 17)]
        self.assertFalse(is_line_changed(9, ranges))
        self.assertFalse(is_line_changed(18, ranges))
        self.assertFalse(is_line_changed(1, ranges))

    def test_is_line_changed_exact_boundaries(self):
        ranges = [(10, 20)]
        self.assertTrue(is_line_changed(10, ranges))
        self.assertTrue(is_line_changed(20, ranges))

    def test_is_line_changed_none_or_empty(self):
        self.assertFalse(is_line_changed(None, [(1, 10)]))
        self.assertFalse(is_line_changed(5, []))

    @patch("app.services.pr_reviewer._github_api_request")
    def test_finding_inside_changed_range_kept(self, mock_api):
        """Finding on line 1 is inside the changed hunk [1, 5] and should be kept."""
        python_content = "import os\n\nx = 1\n"
        patch = "@@ -0,0 +1,5 @@\n+import os\n"

        mock_api.side_effect = [
            {"number": 201, "state": "open"},
            [_make_pr_file("src/app.py", status="modified", patch=patch)],
            {"path": "src/app.py", "sha": "a1", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 201, repository_id=10)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].rule_id, "PY-UNUSED-IMPORT")
        self.assertEqual(result.findings[0].line_number, 1)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_finding_outside_changed_range_removed(self, mock_api):
        """Pre-existing finding on line 1 is outside the PR changed hunk [20, 25] and must be filtered out."""
        # Line 1 has unused import, but the PR only modified lines 20-25
        python_lines = ["import os"] + [f"x_{i} = {i}" for i in range(2, 30)]
        python_content = "\n".join(python_lines) + "\n"
        patch = "@@ -20,4 +20,6 @@\n+x_20 = 99\n"

        mock_api.side_effect = [
            {"number": 202, "state": "open"},
            [_make_pr_file("src/app.py", status="modified", patch=patch)],
            {"path": "src/app.py", "sha": "a2", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 202, repository_id=10)
        # Line 1 finding must be removed because it is outside the PR diff
        self.assertEqual(len(result.findings), 0)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_finding_on_exact_boundary_kept(self, mock_api):
        """Finding on the exact start or end line of a hunk is kept."""
        # Unused import on line 10, hunk starts at line 10
        python_lines = [f"a_{i} = {i}" for i in range(1, 10)] + ["import sys", "b = 1"]
        python_content = "\n".join(python_lines) + "\n"
        patch = "@@ -10,2 +10,3 @@\n+import sys\n"

        mock_api.side_effect = [
            {"number": 203, "state": "open"},
            [_make_pr_file("src/boundary.py", status="modified", patch=patch)],
            {"path": "src/boundary.py", "sha": "a3", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 203, repository_id=10)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].line_number, 10)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_deleted_lines_not_treated_as_changed(self, mock_api):
        """Hunk with only deletions (+10,0) does not keep pre-existing findings."""
        python_content = "import os\n\nx = 1\n"
        patch = "@@ -1,4 +1,0 @@\n-import os\n"

        mock_api.side_effect = [
            {"number": 204, "state": "open"},
            [_make_pr_file("src/deleted_hunk.py", status="modified", patch=patch)],
            {"path": "src/deleted_hunk.py", "sha": "a4", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 204, repository_id=10)
        # No lines added or changed in new file -> 0 findings kept
        self.assertEqual(len(result.findings), 0)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_empty_patch_produces_no_findings(self, mock_api):
        """An empty patch string indicates no lines were changed, so all findings are filtered."""
        python_content = "import os\nx = 1\n"

        mock_api.side_effect = [
            {"number": 205, "state": "open"},
            [_make_pr_file("src/empty.py", status="modified", patch="")],
            {"path": "src/empty.py", "sha": "a5", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 205, repository_id=10)
        self.assertEqual(len(result.findings), 0)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_malformed_patch_safely_produces_no_findings(self, mock_api):
        """Malformed patch text does not crash the reviewer and filters findings safely."""
        python_content = "import os\nx = 1\n"

        mock_api.side_effect = [
            {"number": 206, "state": "open"},
            [_make_pr_file("src/malformed.py", status="modified", patch="not @@ valid @@ patch")],
            {"path": "src/malformed.py", "sha": "a6", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 206, repository_id=10)
        self.assertEqual(len(result.findings), 0)

    @patch("app.services.pr_reviewer._github_api_request")
    def test_file_with_no_patch_preserves_existing_behavior(self, mock_api):
        """When GitHub does not provide a patch (patch is None), all findings are preserved."""
        python_content = "import os\nx = 1\n"

        mock_api.side_effect = [
            {"number": 207, "state": "open"},
            [_make_pr_file("src/no_patch.py", status="modified", patch=None)],
            {"path": "src/no_patch.py", "sha": "a7", "size": len(python_content),
             "content": _base64_encode(python_content)},
        ]

        result = review_pull_request("owner", "repo", 207, repository_id=10)
        # Without a patch, fallback preserves all findings
        self.assertTrue(len(result.findings) >= 1)
        self.assertEqual(result.findings[0].rule_id, "PY-UNUSED-IMPORT")

    @patch("app.services.pr_reviewer._github_api_request")
    def test_multiple_files_with_diff_filtering(self, mock_api):
        """PR with two files: one has finding in diff (kept), one has finding outside diff (removed)."""
        content_a = "import os\nx = 1\n"  # finding on line 1
        patch_a = "@@ -1,2 +1,3 @@\n+import os\n"  # covers line 1

        content_b = "import sys\n" + "\n".join([f"y_{i} = {i}" for i in range(2, 20)])  # finding on line 1
        patch_b = "@@ -10,3 +10,5 @@\n+y_10 = 99\n"  # covers line 10-14, line 1 is outside

        mock_api.side_effect = [
            {"number": 208, "state": "open"},
            [
                _make_pr_file("src/file_a.py", status="modified", patch=patch_a),
                _make_pr_file("src/file_b.py", status="modified", patch=patch_b),
            ],
            {"path": "src/file_a.py", "sha": "fa", "size": len(content_a),
             "content": _base64_encode(content_a)},
            {"path": "src/file_b.py", "sha": "fb", "size": len(content_b),
             "content": _base64_encode(content_b)},
        ]

        result = review_pull_request("owner", "repo", 208, repository_id=10)
        self.assertEqual(result.files_analyzed, 2)
        # Only file_a's finding should be kept; file_b's finding is outside its diff
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].file_path, "src/file_a.py")

    @patch("app.services.pr_reviewer._github_api_request")
    def test_renamed_file_with_diff(self, mock_api):
        """Renamed file with patch filters findings to changed lines using new filename."""
        content = "console.log('debug');\nconst a = 1;\n"
        patch = "@@ -1,2 +1,2 @@\n+console.log('debug');\n"

        mock_api.side_effect = [
            {"number": 209, "state": "open"},
            [_make_pr_file(
                "src/new_name.js",
                status="renamed",
                patch=patch,
                previous_filename="src/old_name.js",
            )],
            {"path": "src/new_name.js", "sha": "rn", "size": len(content),
             "content": _base64_encode(content)},
        ]

        result = review_pull_request("owner", "repo", 209, repository_id=10)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].file_path, "src/new_name.js")
        self.assertEqual(result.findings[0].rule_id, "JS-CONSOLE-LOG")


if __name__ == "__main__":
    unittest.main()

