"""Tests for the GitHub PR Review Comment Integration feature.

Tests Markdown generation, GitHub comment posting, API endpoint integration,
error handling (404, 429, 502, 503), zero/multiple findings handling,
database safety (zero writes), and GitHub safety (no branches/commits/merges).
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from app.core.config import settings
from app.db.database import SessionLocal
from app.main import app
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.schemas.finding import PRCommentResponse
from app.services.github import GitHubAPIError, GitHubRateLimitError
from app.services.pr_commenter import (
    GitHubCredentialsUnavailableError,
    PRCommenterError,
    create_pr_review_comment,
    format_pr_review_comment,
    post_github_pr_comment,
)
from app.services.pr_reviewer import (
    PRFinding,
    PRNotFoundError,
    PRReviewResult,
)


class TestPRCommenterMarkdown(unittest.TestCase):
    """Unit tests for Markdown comment formatting."""

    def test_markdown_with_zero_findings(self):
        """Zero findings produces clean markdown with 'No critical issues detected'."""
        md = format_pr_review_comment(
            summary="All clean!",
            risk_level="low",
            overall_assessment="Code looks great.",
            key_findings=[],
            recommendations=["Ready to merge."],
            include_findings=True,
        )
        self.assertIn("## 🔍 CodeLens AI PR Review", md)
        self.assertIn("**Risk Level:** 🟢 `LOW`", md)
        self.assertIn("> All clean!", md)
        self.assertIn("### 📋 Overall Assessment", md)
        self.assertIn("Code looks great.", md)
        self.assertIn("### 🚨 Key Findings (0)", md)
        self.assertIn("No critical issues detected", md)
        self.assertIn("### 💡 Prioritized Recommendations", md)
        self.assertIn("1. Ready to merge.", md)
        self.assertIn("CodeLens", md)

    def test_markdown_with_multiple_findings(self):
        """Multiple findings outputs all 7 required finding fields."""
        findings = [
            {
                "severity": "error",
                "category": "security",
                "file_path": "src/auth.py",
                "line_number": 42,
                "issue": "Hardcoded secret key",
                "impact": "Credentials exposed in repo",
                "recommendation": "Use os.environ['SECRET_KEY']",
            },
            {
                "severity": "warning",
                "category": "bug",
                "file_path": "src/utils.py",
                "line_number": 15,
                "issue": "Missing null check",
                "impact": "Potential TypeError at runtime",
                "recommendation": "Check if value is not None",
            },
        ]
        md = format_pr_review_comment(
            summary="Security issue identified.",
            risk_level="high",
            overall_assessment="Address security defect before merge.",
            key_findings=findings,
            recommendations=["Rotate exposed secret.", "Add unit tests."],
            include_findings=True,
        )
        self.assertIn("**Risk Level:** 🟠 `HIGH`", md)
        self.assertIn("### 🚨 Key Findings (2)", md)
        # Check finding 1
        self.assertIn("[ERROR] `src/auth.py:42` — Security", md)
        self.assertIn("- **Issue:** Hardcoded secret key", md)
        self.assertIn("- **Impact:** Credentials exposed in repo", md)
        self.assertIn("- **Recommendation:** Use os.environ['SECRET_KEY']", md)
        # Check finding 2
        self.assertIn("[WARNING] `src/utils.py:15` — Bug", md)
        self.assertIn("- **Issue:** Missing null check", md)
        self.assertIn("- **Impact:** Potential TypeError at runtime", md)
        self.assertIn("- **Recommendation:** Check if value is not None", md)

    def test_markdown_with_include_findings_false(self):
        """When include_findings is False, key findings are omitted."""
        findings = [
            {
                "severity": "error",
                "category": "security",
                "file_path": "src/auth.py",
                "line_number": 42,
                "issue": "Hardcoded secret",
                "impact": "Exposed credentials",
                "recommendation": "Fix it",
            }
        ]
        md = format_pr_review_comment(
            summary="Review summary",
            risk_level="medium",
            overall_assessment="Overall assessment text",
            key_findings=findings,
            recommendations=["Some recommendation"],
            include_findings=False,
        )
        self.assertNotIn("Key Findings", md)
        self.assertNotIn("Hardcoded secret", md)
        self.assertIn("Overall assessment text", md)


class TestPostGitHubPRComment(unittest.TestCase):
    """Unit tests for GitHub comment HTTP posting."""

    def test_missing_token_raises_credentials_unavailable(self):
        """When token is None, raises GitHubCredentialsUnavailableError."""
        with patch.object(settings, "github_token", None):
            with self.assertRaises(GitHubCredentialsUnavailableError):
                post_github_pr_comment(
                    owner="test-owner",
                    repo="test-repo",
                    pr_number=1,
                    body="Hello",
                    token=None,
                )

    def test_successful_post(self):
        """Successful POST returns parsed comment response dict with id and html_url."""
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.return_value = json.dumps({
            "id": 987654,
            "html_url": "https://github.com/test-owner/test-repo/pull/1#issuecomment-987654",
        }).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = post_github_pr_comment(
                owner="test-owner",
                repo="test-repo",
                pr_number=1,
                body="Review comment",
                token="mock-token",
            )

        self.assertEqual(result["id"], 987654)
        self.assertIn("issuecomment-987654", result["html_url"])

    def test_post_404_raises_pr_not_found(self):
        """404 HTTPError raises PRNotFoundError."""
        import urllib.error
        http_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/o/r/issues/1/comments",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(PRNotFoundError):
                post_github_pr_comment(
                    owner="o",
                    repo="r",
                    pr_number=1,
                    body="Comment",
                    token="mock-token",
                )

    def test_post_401_raises_credentials_unavailable(self):
        """401 HTTPError raises GitHubCredentialsUnavailableError."""
        import urllib.error
        http_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/o/r/issues/1/comments",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(GitHubCredentialsUnavailableError):
                post_github_pr_comment(
                    owner="o",
                    repo="r",
                    pr_number=1,
                    body="Comment",
                    token="invalid-token",
                )

    def test_post_429_or_rate_limit_raises_github_rate_limit_error(self):
        """403 HTTPError with rate limit header raises GitHubRateLimitError."""
        import urllib.error
        http_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/o/r/issues/1/comments",
            code=403,
            msg="rate limit exceeded",
            hdrs={"x-ratelimit-remaining": "0"},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(GitHubRateLimitError):
                post_github_pr_comment(
                    owner="o",
                    repo="r",
                    pr_number=1,
                    body="Comment",
                    token="mock-token",
                )


class TestPRCommentEndpoint(unittest.TestCase):
    """Integration tests for POST /repositories/{id}/pull-requests/{pr}/comment."""

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._cleanup()
        self.repo = self._create_repo("test-pr-comment-repo")

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "test-commenter-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "test-commenter-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "test-commenter-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str = "test-pr-comment-repo") -> Repository:
        repo = Repository(
            github_id=f"test-commenter-org/{name}",
            name=name,
            full_name=f"test-commenter-org/{name}",
            owner="test-commenter-org",
            url=f"https://github.com/test-commenter-org/{name}",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    def test_comment_endpoint_success_with_findings(self):
        """Happy path with findings: posts comment, returns 200 with PRCommentResponse."""
        fake_review = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=12,
            files_changed=1,
            files_analyzed=1,
            findings=[
                PRFinding(
                    file_path="app.py",
                    line_number=5,
                    severity="error",
                    category="security",
                    message="Dangerous eval() call",
                    rule_id="PY-EVAL",
                )
            ],
            file_contents={"app.py": "eval(x)\n"},
        )

        mock_post_comment = {
            "id": 112233,
            "html_url": "https://github.com/test-commenter-org/test-pr-comment-repo/pull/12#issuecomment-112233",
        }

        with patch.object(settings, "github_token", "valid-test-token"), \
             patch.object(settings, "gemini_api_key", None), \
             patch("app.services.pr_commenter.review_pull_request", return_value=fake_review), \
             patch("app.services.pr_commenter.post_github_pr_comment", return_value=mock_post_comment) as mock_post:
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/12/comment",
                json={
                    "summary": "Custom security summary",
                    "include_findings": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["repository_id"], self.repo.id)
        self.assertEqual(data["pull_request_number"], 12)
        self.assertEqual(data["comment_id"], 112233)
        self.assertIn("issuecomment-112233", data["comment_url"])
        self.assertEqual(data["findings_posted"], 1)

        # Verify comment body passed to post_github_pr_comment
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        posted_body = kwargs.get("body") or mock_post.call_args[0][3]
        self.assertIn("Custom security summary", posted_body)
        self.assertIn("Dangerous eval() call", posted_body)

    def test_comment_endpoint_success_with_ai_review(self):
        """When AI review is enabled and succeeds, AI findings and assessments are included."""
        fake_review = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=12,
            files_changed=1,
            files_analyzed=1,
            findings=[],
            file_contents={"app.py": "x = 1\n"},
        )

        fake_ai_review = {
            "summary": "AI identified no risks.",
            "risk_level": "low",
            "overall_assessment": "Clean pull request.",
            "key_findings": [
                {
                    "severity": "info",
                    "category": "style",
                    "file_path": "app.py",
                    "line_number": 1,
                    "issue": "Add module docstring",
                    "impact": "Improves code readability",
                    "recommendation": "Add a top-level docstring",
                }
            ],
            "recommendations": ["Merge when ready."],
        }

        mock_post_comment = {
            "id": 998877,
            "html_url": "https://github.com/test-commenter-org/test-pr-comment-repo/pull/12#issuecomment-998877",
        }

        with patch.object(settings, "github_token", "valid-test-token"), \
             patch.object(settings, "gemini_api_key", "valid-gemini-key"), \
             patch("app.services.pr_commenter.review_pull_request", return_value=fake_review), \
             patch("app.services.pr_commenter.generate_ai_pr_review", return_value=fake_ai_review), \
             patch("app.services.pr_commenter.post_github_pr_comment", return_value=mock_post_comment) as mock_post:
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/12/comment",
                json={
                    "include_findings": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["findings_posted"], 1)
        self.assertEqual(data["risk_level"], "low")
        _, kwargs = mock_post.call_args
        posted_body = kwargs.get("body") or mock_post.call_args[0][3]
        self.assertIn("Add module docstring", posted_body)

    def test_comment_endpoint_success_with_zero_findings(self):
        """Happy path with zero findings: returns 200, findings_posted=0."""
        fake_review = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=8,
            files_changed=1,
            files_analyzed=1,
            findings=[],
            file_contents={"clean.py": "print('clean')\n"},
        )

        mock_post_comment = {
            "id": 445566,
            "html_url": "https://github.com/test-commenter-org/test-pr-comment-repo/pull/8#issuecomment-445566",
        }

        with patch.object(settings, "github_token", "valid-test-token"), \
             patch("app.services.pr_commenter.review_pull_request", return_value=fake_review), \
             patch("app.services.pr_commenter.post_github_pr_comment", return_value=mock_post_comment):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/8/comment",
                json={},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["findings_posted"], 0)
        self.assertEqual(data["comment_id"], 445566)

    def test_comment_endpoint_include_findings_false(self):
        """When include_findings is False, findings_posted is 0."""
        fake_review = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=9,
            files_changed=1,
            files_analyzed=1,
            findings=[
                PRFinding(
                    file_path="app.py",
                    line_number=1,
                    severity="warning",
                    category="style",
                    message="Line too long",
                    rule_id="PEP8-LINE-LENGTH",
                )
            ],
            file_contents={"app.py": "x = 1\n"},
        )

        mock_post_comment = {
            "id": 778899,
            "html_url": "https://github.com/test-commenter-org/test-pr-comment-repo/pull/9#issuecomment-778899",
        }

        with patch.object(settings, "github_token", "valid-test-token"), \
             patch("app.services.pr_commenter.review_pull_request", return_value=fake_review), \
             patch("app.services.pr_commenter.post_github_pr_comment", return_value=mock_post_comment):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/9/comment",
                json={"include_findings": False},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["findings_posted"], 0)

    def test_comment_endpoint_repository_not_found(self):
        """Returns 404 when repository ID does not exist."""
        response = self.client.post(
            "/repositories/999999/pull-requests/1/comment",
            json={},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Repository with id 999999 not found", response.json()["detail"])

    def test_comment_endpoint_pr_not_found(self):
        """Returns 404 when PR does not exist on GitHub."""
        with patch.object(settings, "github_token", "valid-test-token"), \
             patch("app.services.pr_commenter.review_pull_request", side_effect=PRNotFoundError("PR #999 not found")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/999/comment",
                json={},
            )
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_comment_endpoint_missing_credentials_returns_503(self):
        """Returns 503 when GITHUB_TOKEN is not configured."""
        with patch.object(settings, "github_token", None):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/1/comment",
                json={},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("missing GITHUB_TOKEN", response.json()["detail"])

    def test_comment_endpoint_rate_limit_returns_429(self):
        """Returns 429 when GitHub rate limit is exceeded."""
        with patch.object(settings, "github_token", "valid-test-token"), \
             patch("app.services.pr_commenter.review_pull_request", side_effect=GitHubRateLimitError("Rate limit exceeded")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/1/comment",
                json={},
            )
        self.assertEqual(response.status_code, 429)

    def test_comment_endpoint_github_api_error_returns_502(self):
        """Returns 502 when GitHub API call fails."""
        with patch.object(settings, "github_token", "valid-test-token"), \
             patch("app.services.pr_commenter.review_pull_request", side_effect=GitHubAPIError("GitHub server down")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/1/comment",
                json={},
            )
        self.assertEqual(response.status_code, 502)

    def test_database_is_not_modified(self):
        """Posting a comment does NOT write or mutate any database rows."""
        initial_findings_count = self.db.query(Finding).count()
        initial_source_files_count = self.db.query(SourceFile).count()

        fake_review = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
            findings=[],
            file_contents={"main.py": "print('hello')\n"},
        )

        mock_post = {"id": 1, "html_url": "https://github.com/..."}

        with patch.object(settings, "github_token", "test-token"), \
             patch.object(settings, "gemini_api_key", None), \
             patch("app.services.pr_commenter.review_pull_request", return_value=fake_review), \
             patch("app.services.pr_commenter.post_github_pr_comment", return_value=mock_post):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/1/comment",
                json={},
            )
            self.assertEqual(response.status_code, 200)

        # Database rows must be identical
        current_findings = self.db.query(Finding).count()
        current_files = self.db.query(SourceFile).count()
        self.assertEqual(current_findings, initial_findings_count)
        self.assertEqual(current_files, initial_source_files_count)

    def test_github_is_never_mutated_except_for_comment(self):
        """Asserts that no branch, file, commit, or merge operations are executed."""
        fake_review = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
            findings=[],
            file_contents={"main.py": "print('hello')\n"},
        )

        with patch.object(settings, "github_token", "test-token"), \
             patch.object(settings, "gemini_api_key", None), \
             patch("app.services.pr_commenter.review_pull_request", return_value=fake_review), \
             patch("urllib.request.urlopen") as mock_urlopen:

            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.read.return_value = json.dumps({"id": 100, "html_url": "url"}).encode("utf-8")
            mock_urlopen.return_value = mock_response

            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/1/comment",
                json={},
            )
            self.assertEqual(response.status_code, 200)

            # Inspect all calls made to urlopen
            for call in mock_urlopen.call_args_list:
                req = call[0][0]
                url = req.full_url if hasattr(req, "full_url") else str(req)
                method = req.get_method() if hasattr(req, "get_method") else "GET"

                # Verify only the comments endpoint was called with POST
                if method == "POST":
                    self.assertIn("/issues/1/comments", url)
                    self.assertNotIn("/git/", url)
                    self.assertNotIn("/commits", url)
                    self.assertNotIn("/branches", url)
                    self.assertNotIn("/merge", url)
