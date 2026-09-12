"""Tests for the AI-powered PR finding fix feature.

Tests prompt construction, Gemini interaction, response parsing,
diff computation, in-memory preview of resulting code, error handling (404, 422, 429, 502, 503),
database safety (zero persistence), and GitHub safety (zero mutations).
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
from app.schemas.finding import PRFindingFixResponse
from app.services.ai_pr_fixer import (
    AIPRFixerError,
    GeminiNotConfiguredError,
    build_pr_fixer_prompt,
    generate_pr_finding_fix,
)
from app.services.github import GitHubAPIError, GitHubRateLimitError
from app.services.pr_reviewer import (
    PRNotFoundError,
    PRReviewResult,
)


def _make_mock_gemini_client(response_text: str):
    """Create a mock Gemini client returning response_text."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_client.models.generate_content.return_value = mock_response
    return mock_client


class TestAIPRFixerService(unittest.TestCase):
    """Unit tests for the AI PR Fixer service."""

    def test_missing_api_key_raises_error(self):
        """When GEMINI_API_KEY is not set and no client is passed, raise GeminiNotConfiguredError."""
        file_contents = {"src/main.py": "print('hello')\n"}
        with patch.object(settings, "gemini_api_key", None):
            with self.assertRaises(GeminiNotConfiguredError):
                generate_pr_finding_fix(
                    file_contents=file_contents,
                    file_path="src/main.py",
                    line_number=1,
                    issue="Test issue",
                    severity="warning",
                    category="style",
                    message="Use functions",
                )

    def test_successful_fix_generation(self):
        """Valid structured JSON response produces a full PRFindingFixResponse."""
        source_code = "def process(user_input):\n    eval(user_input)\n"
        file_contents = {"app/eval.py": source_code}

        mock_payload = {
            "explanation": "Replace dangerous eval() with ast.literal_eval() for safe parsing.",
            "original_code": "    eval(user_input)",
            "fixed_code": "    ast.literal_eval(user_input)",
            "diff": "--- a/app/eval.py\n+++ b/app/eval.py\n@@ -2,1 +2,1 @@\n-    eval(user_input)\n+    ast.literal_eval(user_input)\n",
        }
        client = _make_mock_gemini_client(json.dumps(mock_payload))

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_pr_finding_fix(
                file_contents=file_contents,
                file_path="app/eval.py",
                line_number=2,
                issue="Dangerous eval usage",
                severity="error",
                category="security",
                message="Avoid eval() to prevent code injection",
                gemini_client=client,
            )

        self.assertIsInstance(result, PRFindingFixResponse)
        self.assertEqual(result.file_path, "app/eval.py")
        self.assertEqual(result.line_number, 2)
        self.assertIn("literal_eval", result.explanation)
        self.assertEqual(result.original_code, "    eval(user_input)")
        self.assertEqual(result.fixed_code, "    ast.literal_eval(user_input)")
        self.assertIsNotNone(result.diff)
        self.assertIn("-    eval(user_input)", result.diff)
        self.assertIn("+    ast.literal_eval(user_input)", result.diff)
        self.assertIsNotNone(result.resulting_code)
        self.assertIn("literal_eval", result.resulting_code)

    def test_fix_with_markdown_fences(self):
        """Service properly parses JSON wrapped in markdown code blocks."""
        source_code = "SECRET = '12345'\n"
        file_contents = {"config.py": source_code}

        raw_json = json.dumps({
            "explanation": "Load credentials from environment variables.",
            "original_code": "SECRET = '12345'",
            "fixed_code": "SECRET = os.getenv('SECRET')",
            "diff": None,
        })
        fenced = f"```json\n{raw_json}\n```"
        client = _make_mock_gemini_client(fenced)

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_pr_finding_fix(
                file_contents=file_contents,
                file_path="config.py",
                line_number=1,
                issue="Hardcoded secret",
                severity="error",
                category="security",
                message="Do not hardcode secrets",
                gemini_client=client,
            )

        self.assertEqual(result.fixed_code, "SECRET = os.getenv('SECRET')")
        self.assertIn("Load credentials", result.explanation)
        # Unified diff should have been automatically computed when diff was None
        self.assertIsNotNone(result.diff)
        self.assertIn("SECRET = '12345'", result.diff)
        self.assertIn("os.getenv", result.diff)

    def test_fix_with_deletion(self):
        """Service handles deletion fixes where fixed_code is empty."""
        source_code = "import unused_module\nprint('hello')\n"
        file_contents = {"main.py": source_code}

        mock_payload = {
            "explanation": "Remove unused import.",
            "original_code": "import unused_module",
            "fixed_code": "",
            "diff": None,
        }
        client = _make_mock_gemini_client(json.dumps(mock_payload))

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_pr_finding_fix(
                file_contents=file_contents,
                file_path="main.py",
                line_number=1,
                issue="Unused import",
                severity="info",
                category="style",
                message="Remove unused import",
                gemini_client=client,
            )

        self.assertEqual(result.fixed_code, "")
        self.assertIsNotNone(result.diff)
        self.assertIn("-import unused_module", result.diff)

    def test_fix_fallback_on_json_error(self):
        """Service falls back gracefully when Gemini returns non-JSON text."""
        source_code = "var x = 1;\n"
        file_contents = {"index.js": source_code}

        client = _make_mock_gemini_client("Use const or let instead of var.")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            result = generate_pr_finding_fix(
                file_contents=file_contents,
                file_path="index.js",
                line_number=1,
                issue="var usage",
                severity="warning",
                category="style",
                message="Prefer const/let",
                gemini_client=client,
            )

        self.assertIn("Use const or let", result.explanation)
        self.assertEqual(result.file_path, "index.js")
        self.assertEqual(result.line_number, 1)

    @patch("app.services.ai_provider.call_ollama", side_effect=RuntimeError("Ollama connection failed"))
    def test_gemini_api_error_raises_aiprfixererror(self, _mock_ollama):
        """When Gemini client raises a generic exception and fallback fails, wrap in AIPRFixerError."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("Connection timeout")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            with self.assertRaises(AIPRFixerError):
                generate_pr_finding_fix(
                    file_contents={"test.py": "x = 1\n"},
                    file_path="test.py",
                    line_number=1,
                    issue="Test",
                    severity="info",
                    category="general",
                    message="Test",
                    gemini_client=mock_client,
                )

    @patch("app.services.ai_provider.call_ollama", side_effect=RuntimeError("Ollama connection failed"))
    def test_gemini_quota_error_raises_ai_quota_exceeded_error(self, _mock_ollama):
        """When Gemini client raises a quota error and fallback fails, raise AIQuotaExceededError."""
        from app.core.ai_errors import AIQuotaExceededError
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded")

        with patch.object(settings, "gemini_api_key", "mock-key"):
            with self.assertRaises(AIQuotaExceededError):
                generate_pr_finding_fix(
                    file_contents={"test.py": "x = 1\n"},
                    file_path="test.py",
                    line_number=1,
                    issue="Test",
                    severity="info",
                    category="general",
                    message="Test",
                    gemini_client=mock_client,
                )

    def test_build_pr_fixer_prompt_contains_finding_details(self):
        """Prompt builder includes all metadata and code context."""
        prompt = build_pr_fixer_prompt(
            file_path="src/service.py",
            line_number=42,
            issue="SQL Injection",
            severity="critical",
            category="security",
            message="Raw string interpolation in query",
            code_context="query = f'SELECT * FROM users WHERE id = {user_id}'",
        )
        self.assertIn("src/service.py", prompt)
        self.assertIn("42", prompt)
        self.assertIn("SQL Injection", prompt)
        self.assertIn("critical", prompt)
        self.assertIn("security", prompt)
        self.assertIn("SELECT * FROM users", prompt)


class TestFixPRFindingEndpoint(unittest.TestCase):
    """Integration tests for POST /repositories/{id}/pull-requests/{pr}/findings/fix."""

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._cleanup()
        self.repo = self._create_repo("test-repo-fixer")

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "ai-pr-fixer-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "ai-pr-fixer-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "ai-pr-fixer-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str = "ai-pr-fixer-repo") -> Repository:
        repo = Repository(
            github_id=f"ai-pr-fixer-org/{name}",
            name=name,
            full_name=f"ai-pr-fixer-org/{name}",
            owner="ai-pr-fixer-org",
            url=f"https://github.com/ai-pr-fixer-org/{name}",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    def test_fix_pr_finding_success(self):
        """Happy path: Returns 200 with PRFindingFixResponse."""
        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "def add(a, b):\n    return a + b\n"},
        )

        mock_payload = {
            "explanation": "Add type annotations to add function.",
            "original_code": "def add(a, b):",
            "fixed_code": "def add(a: int, b: int) -> int:",
            "diff": "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,1 @@\n-def add(a, b):\n+def add(a: int, b: int) -> int:\n",
        }
        mock_gemini = _make_mock_gemini_client(json.dumps(mock_payload))

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result), \
             patch("app.api.routes.repositories.generate_pr_finding_fix") as mock_fix:
            mock_fix.return_value = PRFindingFixResponse(
                file_path="src/calc.py",
                line_number=1,
                explanation="Add type annotations to add function.",
                original_code="def add(a, b):",
                fixed_code="def add(a: int, b: int) -> int:",
                diff=mock_payload["diff"],
                resulting_code="1 | def add(a: int, b: int) -> int:\n2 |     return a + b",
            )

            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Missing type annotations",
                    "severity": "info",
                    "category": "style",
                    "message": "Add type hints",
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["file_path"], "src/calc.py")
        self.assertEqual(data["line_number"], 1)
        self.assertEqual(data["original_code"], "def add(a, b):")
        self.assertEqual(data["fixed_code"], "def add(a: int, b: int) -> int:")
        self.assertIn("--- a/src/calc.py", data["diff"])
        self.assertIn("resulting_code", data)

    def test_fix_pr_finding_endpoint_end_to_end(self):
        """End-to-end endpoint test: calls generate_pr_finding_fix with mock Gemini client."""
        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "def add(a, b):\n    return a + b\n"},
        )
        mock_payload = {
            "explanation": "Add type annotations to add function.",
            "original_code": "def add(a, b):",
            "fixed_code": "def add(a: int, b: int) -> int:",
            "diff": "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,1 @@\n-def add(a, b):\n+def add(a: int, b: int) -> int:\n",
        }
        mock_client = _make_mock_gemini_client(json.dumps(mock_payload))

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result), \
             patch("google.genai.Client", return_value=mock_client):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Missing type annotations",
                    "severity": "info",
                    "category": "style",
                    "message": "Add type hints",
                },
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["file_path"], "src/calc.py")
        self.assertEqual(data["line_number"], 1)
        self.assertIn("add(a: int, b: int)", data["fixed_code"])
        self.assertIn("resulting_code", data)

    def test_repository_not_found(self):
        """Returns 404 when repository does not exist."""
        response = self.client.post(
            "/repositories/999999/pull-requests/1/findings/fix",
            json={
                "file_path": "test.py",
                "line_number": 1,
                "issue": "Issue",
                "severity": "info",
                "category": "style",
                "message": "Message",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Repository with id 999999 not found", response.json()["detail"])

    def test_pr_not_found(self):
        """Returns 404 when PR is not found on GitHub."""
        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", side_effect=PRNotFoundError("PR 999 not found")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/999/findings/fix",
                json={
                    "file_path": "test.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_file_not_in_pr(self):
        """Returns 404 when file_path is not among the PR changed files."""
        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "x = 1\n"},
        )

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "other/file.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )

        self.assertEqual(response.status_code, 404)
        self.assertIn("other/file.py", response.json()["detail"])

    def test_invalid_line_number_out_of_bounds(self):
        """Returns 422 when line_number exceeds the file length."""
        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "line1\nline2\nline3\n"},
        )

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 100,  # File only has 3 lines
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("100 is invalid", response.json()["detail"])

    def test_invalid_line_number_negative_or_zero(self):
        """Returns 422 when line_number is less than 1."""
        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "line1\n"},
        )

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 0,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )

        self.assertEqual(response.status_code, 422)

    def test_gemini_not_configured_returns_503(self):
        """Returns 503 when GEMINI_API_KEY is not configured."""
        with patch.object(settings, "gemini_api_key", None):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("missing GEMINI_API_KEY", response.json()["detail"])

    def test_gemini_service_error_returns_502(self):
        """Returns 502 when AI PR fixer service encounters an error."""
        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "x = 1\n"},
        )

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result), \
             patch("app.api.routes.repositories.generate_pr_finding_fix", side_effect=AIPRFixerError("Model timeout")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Model timeout", response.json()["detail"])

    def test_github_rate_limit_returns_429(self):
        """Returns 429 when GitHub rate limit is exceeded."""
        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", side_effect=GitHubRateLimitError("Rate limited")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )
        self.assertEqual(response.status_code, 429)

    def test_github_api_error_returns_502(self):
        """Returns 502 when GitHub API fails unexpectedly."""
        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", side_effect=GitHubAPIError("GitHub down")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )
        self.assertEqual(response.status_code, 502)

    def test_database_is_not_modified(self):
        """Fix generation does not write or mutate any database records."""
        initial_findings_count = self.db.query(Finding).count()
        initial_source_files_count = self.db.query(SourceFile).count()

        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "x = 1\n"},
        )

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result), \
             patch("app.api.routes.repositories.generate_pr_finding_fix") as mock_fix:
            mock_fix.return_value = PRFindingFixResponse(
                file_path="src/calc.py",
                line_number=1,
                explanation="Fix explanation",
                original_code="x = 1",
                fixed_code="x = 2",
            )
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )

        self.assertEqual(response.status_code, 200)

        # Confirm count of findings and source_files is completely unchanged
        current_findings_count = self.db.query(Finding).count()
        current_source_files_count = self.db.query(SourceFile).count()
        self.assertEqual(current_findings_count, initial_findings_count)
        self.assertEqual(current_source_files_count, initial_source_files_count)

    def test_github_is_not_mutated(self):
        """Fix generation never sends write/mutation requests to GitHub."""
        fake_review_result = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=5,
            files_changed=1,
            files_analyzed=1,
            file_contents={"src/calc.py": "x = 1\n"},
        )

        with patch.object(settings, "gemini_api_key", "test-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_review_result), \
             patch("app.api.routes.repositories.generate_pr_finding_fix") as mock_fix, \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_fix.return_value = PRFindingFixResponse(
                file_path="src/calc.py",
                line_number=1,
                explanation="Fix explanation",
                original_code="x = 1",
                fixed_code="x = 2",
            )
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/5/findings/fix",
                json={
                    "file_path": "src/calc.py",
                    "line_number": 1,
                    "issue": "Issue",
                    "severity": "info",
                    "category": "style",
                    "message": "Message",
                },
            )

        self.assertEqual(response.status_code, 200)
        # Verify no network mutations were invoked directly
        mock_urlopen.assert_not_called()

