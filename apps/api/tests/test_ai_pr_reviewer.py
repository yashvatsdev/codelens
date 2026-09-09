"""Tests for the AI-powered Pull Request review feature (Phase 2).

Tests the AI PR review service, the API endpoint, Gemini prompt construction,
response parsing (including markdown fences and malformed responses), database safety,
and regression safety with Phase 1 and existing AI features.
"""

import base64
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
from app.services.ai_pr_reviewer import (
    AIPRReviewerError,
    GeminiNotConfiguredError,
    build_ai_pr_review_prompt,
    generate_ai_pr_review,
)
from app.services.github import GitHubAPIError, GitHubRateLimitError
from app.services.pr_reviewer import (
    PRFinding,
    PRNotFoundError,
    PRReviewResult,
    review_pull_request,
)


def _base64_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _make_mock_gemini_client(response_text: str):
    """Create a mock Gemini client returning response_text."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_client.models.generate_content.return_value = mock_response
    return mock_client


class TestAIPRReviewerService(unittest.TestCase):
    """Unit tests for the AI PR review service."""

    def test_missing_api_key_raises_error(self):
        """When GEMINI_API_KEY is not set and no client is passed, raise GeminiNotConfiguredError."""
        review_result = PRReviewResult(
            repository_id=1,
            pull_request_number=10,
            files_changed=1,
            files_analyzed=1,
        )
        with patch.object(settings, "gemini_api_key", None):
            with self.assertRaises(GeminiNotConfiguredError):
                generate_ai_pr_review(review_result, "owner/repo")

    def test_successful_ai_pr_review_parsing(self):
        """Valid structured JSON response is parsed into expected schema."""
        review_result = PRReviewResult(
            repository_id=1,
            pull_request_number=10,
            files_changed=2,
            files_analyzed=2,
            findings=[
                PRFinding(
                    file_path="src/app.py",
                    line_number=12,
                    severity="error",
                    category="security",
                    message="Dangerous eval() usage detected",
                    rule_id="JS-EVAL-USAGE",
                )
            ],
            file_contents={"src/app.py": "x = 1\neval(cmd)\n"},
        )

        mock_gemini_payload = {
            "summary": "This PR introduces a critical security vulnerability with eval().",
            "risk_level": "critical",
            "overall_assessment": "The changes look generally fine except for dynamic execution in src/app.py.",
            "key_findings": [
                {
                    "file_path": "src/app.py",
                    "line_number": 12,
                    "severity": "error",
                    "category": "security",
                    "issue": "Use of eval() allows arbitrary code execution.",
                    "impact": "An attacker controlling input could execute arbitrary code.",
                    "recommendation": "Replace eval() with safe parsing logic.",
                }
            ],
            "recommendations": [
                "Remove eval() from src/app.py immediately.",
                "Add unit tests verifying command input validation.",
            ],
        }

        mock_client = _make_mock_gemini_client(json.dumps(mock_gemini_payload))

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_ai_pr_review(
                review_result=review_result,
                repo_full_name="owner/repo",
                gemini_client=mock_client,
            )

        self.assertEqual(result["risk_level"], "critical")
        self.assertIn("critical security vulnerability", result["summary"])
        self.assertEqual(len(result["key_findings"]), 1)
        self.assertEqual(result["key_findings"][0]["file_path"], "src/app.py")
        self.assertEqual(result["key_findings"][0]["severity"], "error")
        self.assertEqual(len(result["recommendations"]), 2)

    def test_prompt_contains_metadata_findings_and_source_context(self):
        """Verify prompt contains PR number, repo name, findings, and code context."""
        review_result = PRReviewResult(
            repository_id=42,
            pull_request_number=15,
            files_changed=3,
            files_analyzed=1,
            findings=[
                PRFinding(
                    file_path="auth/jwt.py",
                    line_number=25,
                    severity="warning",
                    category="bug",
                    message="Bare except clause used",
                    rule_id="PY-BARE-EXCEPT",
                )
            ],
            file_contents={
                "auth/jwt.py": "\n".join([f"line_{i} = {i}" for i in range(1, 40)])
            },
        )

        mock_client = _make_mock_gemini_client(json.dumps({
            "summary": "OK",
            "risk_level": "medium",
            "overall_assessment": "Moderate risk",
            "key_findings": [],
            "recommendations": [],
        }))

        with patch.object(settings, "gemini_api_key", "mock-key"):
            generate_ai_pr_review(
                review_result=review_result,
                repo_full_name="acme/platform",
                gemini_client=mock_client,
            )

        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        prompt = call_kwargs["contents"]

        self.assertIn("acme/platform", prompt)
        self.assertIn("#15", prompt)
        self.assertIn("auth/jwt.py", prompt)
        self.assertIn("PY-BARE-EXCEPT", prompt)
        self.assertIn("Bare except clause used", prompt)
        self.assertIn("line_25", prompt)

    def test_json_in_markdown_fences_parsed_correctly(self):
        """Responses wrapped in markdown ```json ... ``` fences parse cleanly."""
        review_result = PRReviewResult(
            repository_id=1,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
        )

        fenced_response = (
            "```json\n"
            "{\n"
            '  "summary": "Fenced summary",\n'
            '  "risk_level": "low",\n'
            '  "overall_assessment": "Clean code",\n'
            '  "key_findings": [],\n'
            '  "recommendations": []\n'
            "}\n"
            "```"
        )

        mock_client = _make_mock_gemini_client(fenced_response)

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_ai_pr_review(
                review_result=review_result,
                repo_full_name="owner/repo",
                gemini_client=mock_client,
            )

        self.assertEqual(result["summary"], "Fenced summary")
        self.assertEqual(result["risk_level"], "low")

    def test_malformed_gemini_response_handled_gracefully(self):
        """Malformed JSON from Gemini does not crash and returns a safe fallback."""
        review_result = PRReviewResult(
            repository_id=1,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
            findings=[
                PRFinding(
                    file_path="test.py",
                    line_number=1,
                    severity="warning",
                    category="style",
                    message="Style issue",
                    rule_id="STYLE-001",
                )
            ],
        )

        mock_client = _make_mock_gemini_client("This is not valid JSON at all!")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_ai_pr_review(
                review_result=review_result,
                repo_full_name="owner/repo",
                gemini_client=mock_client,
            )

        self.assertEqual(result["risk_level"], "medium")
        self.assertIn("This is not valid JSON", result["summary"])
        self.assertEqual(result["key_findings"], [])

    def test_empty_findings_produces_valid_low_risk_review(self):
        """When PR has no static findings, prompt reflects that and result is valid low risk."""
        review_result = PRReviewResult(
            repository_id=1,
            pull_request_number=5,
            files_changed=3,
            files_analyzed=3,
            findings=[],
            file_contents={},
        )

        mock_gemini_payload = {
            "summary": "No static analysis issues detected in this PR.",
            "risk_level": "low",
            "overall_assessment": "The changes adhere to project standards with no detected defects.",
            "key_findings": [],
            "recommendations": [],
        }

        mock_client = _make_mock_gemini_client(json.dumps(mock_gemini_payload))

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_ai_pr_review(
                review_result=review_result,
                repo_full_name="owner/repo",
                gemini_client=mock_client,
            )

        self.assertEqual(result["risk_level"], "low")
        self.assertIn("No static analysis issues", result["summary"])
        self.assertEqual(len(result["key_findings"]), 0)

    def test_gemini_api_failure_raises_aiprreviewer_error(self):
        """API network/service error raises AIPRReviewerError."""
        review_result = PRReviewResult(
            repository_id=1,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("Google API 500")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            with self.assertRaises(AIPRReviewerError):
                generate_ai_pr_review(
                    review_result=review_result,
                    repo_full_name="owner/repo",
                    gemini_client=mock_client,
                )


class TestAIPRReviewEndpoint(unittest.TestCase):
    """Integration tests for POST /repositories/{id}/pull-requests/{pr}/ai-review."""

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "ai-pr-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "ai-pr-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "ai-pr-test-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str = "ai-pr-repo") -> Repository:
        repo = Repository(
            github_id=f"ai-pr-test-org/{name}",
            name=name,
            full_name=f"ai-pr-test-org/{name}",
            owner="ai-pr-test-org",
            url=f"https://github.com/ai-pr-test-org/{name}",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    def test_repository_not_found_returns_404(self):
        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post("/repositories/999999/pull-requests/1/ai-review")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn("not found", data["detail"].lower())

    def test_missing_api_key_returns_503(self):
        repo = self._create_repo()
        with patch.object(settings, "gemini_api_key", None):
            response = self.client.post(f"/repositories/{repo.id}/pull-requests/1/ai-review")
            self.assertEqual(response.status_code, 503)
            data = response.json()
            self.assertIn("Gemini API is not configured", data["detail"])

    @patch("app.api.routes.repositories.review_pull_request")
    def test_pr_not_found_returns_404(self, mock_review):
        repo = self._create_repo()
        mock_review.side_effect = PRNotFoundError("PR #999 not found")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/pull-requests/999/ai-review")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn("999", data["detail"])

    @patch("app.api.routes.repositories.review_pull_request")
    def test_github_api_error_returns_502(self, mock_review):
        repo = self._create_repo()
        mock_review.side_effect = GitHubAPIError("upstream failure")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/pull-requests/1/ai-review")
            self.assertEqual(response.status_code, 502)

    @patch("app.api.routes.repositories.review_pull_request")
    def test_github_rate_limit_returns_429(self, mock_review):
        repo = self._create_repo()
        mock_review.side_effect = GitHubRateLimitError("rate limit exceeded")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/pull-requests/1/ai-review")
            self.assertEqual(response.status_code, 429)

    @patch("app.api.routes.repositories.generate_ai_pr_review")
    @patch("app.api.routes.repositories.review_pull_request")
    def test_gemini_failure_returns_502(self, mock_review, mock_ai_review):
        repo = self._create_repo()
        mock_review.return_value = PRReviewResult(
            repository_id=repo.id,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
        )
        mock_ai_review.side_effect = AIPRReviewerError("Gemini quota exceeded")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/pull-requests/1/ai-review")
            self.assertEqual(response.status_code, 502)
            data = response.json()
            self.assertIn("Gemini quota exceeded", data["detail"])

    @patch("google.genai.Client")
    @patch("app.api.routes.repositories.review_pull_request")
    def test_successful_ai_review_endpoint(self, mock_review, mock_client_cls):
        """End-to-end endpoint test verifying response matches AIPRReviewResponse schema."""
        repo = self._create_repo()
        mock_review.return_value = PRReviewResult(
            repository_id=repo.id,
            pull_request_number=7,
            files_changed=2,
            files_analyzed=2,
            findings=[
                PRFinding(
                    file_path="src/index.js",
                    line_number=3,
                    severity="warning",
                    category="style",
                    message="Unexpected console.log statement",
                    rule_id="JS-CONSOLE-LOG",
                )
            ],
            file_contents={"src/index.js": "const a = 1;\nconsole.log(a);\n"},
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "summary": "Minor logging cleanup required.",
            "risk_level": "low",
            "overall_assessment": "The PR looks good overall with minor style issues.",
            "key_findings": [
                {
                    "file_path": "src/index.js",
                    "line_number": 3,
                    "severity": "warning",
                    "category": "style",
                    "issue": "console.log left in production code",
                    "impact": "Logs can clutter production outputs.",
                    "recommendation": "Remove console.log before merging.",
                }
            ],
            "recommendations": ["Remove console.log."],
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/pull-requests/7/ai-review")
            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertEqual(data["summary"], "Minor logging cleanup required.")
            self.assertEqual(data["risk_level"], "low")
            self.assertEqual(len(data["key_findings"]), 1)
            self.assertEqual(data["key_findings"][0]["file_path"], "src/index.js")
            self.assertEqual(data["key_findings"][0]["recommendation"], "Remove console.log before merging.")
            self.assertEqual(data["recommendations"], ["Remove console.log."])

    @patch("google.genai.Client")
    @patch("app.api.routes.repositories.review_pull_request")
    def test_no_database_records_created_or_modified(self, mock_review, mock_client_cls):
        """Verify no Finding, SourceFile, or Repository records are created or altered."""
        repo = self._create_repo()
        mock_review.return_value = PRReviewResult(
            repository_id=repo.id,
            pull_request_number=8,
            files_changed=1,
            files_analyzed=1,
            findings=[
                PRFinding(
                    file_path="src/eval.py",
                    line_number=2,
                    severity="error",
                    category="security",
                    message="eval usage",
                    rule_id="JS-EVAL-USAGE",
                )
            ],
            file_contents={"src/eval.py": "eval('2+2')\n"},
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "summary": "Critical eval detected.",
            "risk_level": "high",
            "overall_assessment": "High risk due to eval.",
            "key_findings": [],
            "recommendations": [],
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        before_findings = self.db.query(Finding).filter(Finding.repository_id == repo.id).count()
        before_sources = self.db.query(SourceFile).filter(SourceFile.repository_id == repo.id).count()

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/pull-requests/8/ai-review")
            self.assertEqual(response.status_code, 200)

        after_findings = self.db.query(Finding).filter(Finding.repository_id == repo.id).count()
        after_sources = self.db.query(SourceFile).filter(SourceFile.repository_id == repo.id).count()

        self.assertEqual(before_findings, 0)
        self.assertEqual(after_findings, 0)
        self.assertEqual(before_sources, after_sources)


class TestRegressionSafety(unittest.TestCase):
    """Verify that Phase 1 PR review and existing AI features still work."""

    @patch("app.services.pr_reviewer._github_api_request")
    def test_phase1_pr_review_still_works_and_stores_file_contents(self, mock_api):
        """Phase 1 review_pull_request still functions properly and populates file_contents."""
        py_content = "import os\nx = 1\n"
        mock_api.side_effect = [
            {"number": 99, "state": "open"},
            [{
                "filename": "app.py",
                "status": "modified",
                "patch": "",
                "contents_url": "https://api.github.com/contents/app.py",
            }],
            {
                "path": "app.py",
                "sha": "a1",
                "size": len(py_content),
                "content": _base64_encode(py_content),
            },
        ]

        result = review_pull_request("owner", "repo", 99, repository_id=50)

        self.assertEqual(result.files_analyzed, 1)
        self.assertTrue(len(result.findings) >= 1)
        # Verify file_contents was populated for AI review reuse
        self.assertIn("app.py", result.file_contents)
        self.assertEqual(result.file_contents["app.py"], py_content)


if __name__ == "__main__":
    unittest.main()

