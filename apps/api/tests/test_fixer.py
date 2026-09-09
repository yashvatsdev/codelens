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
from app.schemas.finding import FindingFixResponse
from app.services.fixer import (
    FixerError,
    GeminiNotConfiguredError,
    build_fixer_prompt,
    compute_unified_diff,
    generate_fix,
)


class TestFixerServiceAndEndpoint(unittest.TestCase):
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
                    Repository.owner == "fixer-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "fixer-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "fixer-test-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str = "test-repo") -> Repository:
        repo = Repository(
            github_id=f"fixer-test-org/{name}",
            name=name,
            full_name=f"fixer-test-org/{name}",
            owner="fixer-test-org",
            url=f"https://github.com/fixer-test-org/{name}",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    def _create_source_file(self, repo_id: int, path: str, content: str) -> SourceFile:
        sf = SourceFile(
            repository_id=repo_id,
            path=path,
            sha="fixertestsha1234567890",
            content=content,
            size=len(content.encode("utf-8")),
        )
        self.db.add(sf)
        self.db.commit()
        self.db.refresh(sf)
        return sf

    def _create_finding(
        self,
        repo_id: int,
        file_path: str = "src/utils.py",
        line_number: int = 5,
        rule_id: str = "JS-DEBUGGER",
        severity: str = "warning",
        category: str = "style",
        message: str = "Debugger statement detected",
    ) -> Finding:
        finding = Finding(
            repository_id=repo_id,
            file_path=file_path,
            line_number=line_number,
            rule_id=rule_id,
            severity=severity,
            category=category,
            message=message,
        )
        self.db.add(finding)
        self.db.commit()
        self.db.refresh(finding)
        return finding

    def test_compute_unified_diff(self):
        original = "def process():\n    debugger\n    return True\n"
        fixed = "def process():\n    return True\n"
        diff = compute_unified_diff(original, fixed, "src/utils.py")
        self.assertIn("--- a/src/utils.py", diff)
        self.assertIn("+++ b/src/utils.py", diff)
        self.assertIn("-    debugger", diff)

    def test_fix_missing_api_key_returns_503(self):
        repo = self._create_repo()
        self._create_source_file(repo.id, "app.py", "x = 1\n")
        finding = self._create_finding(repo.id, file_path="app.py", line_number=1)

        with patch.object(settings, "gemini_api_key", None):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/fix")
            self.assertEqual(response.status_code, 503)
            data = response.json()
            self.assertIn("Gemini API is not configured", data["detail"])

    def test_fix_missing_repository_returns_404(self):
        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post("/repositories/999999/findings/1/fix")
            self.assertEqual(response.status_code, 404)

    def test_fix_missing_finding_returns_404(self):
        repo = self._create_repo()
        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/999999/fix")
            self.assertEqual(response.status_code, 404)

    def test_fix_finding_belonging_to_another_repo_returns_404(self):
        repo_a = self._create_repo("repo-a")
        repo_b = self._create_repo("repo-b")
        finding_a = self._create_finding(repo_a.id, file_path="a.py", line_number=1)

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo_b.id}/findings/{finding_a.id}/fix")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn(f"Finding with id {finding_a.id} not found for repository {repo_b.id}", data["detail"])

    def test_fix_missing_source_file_returns_404(self):
        repo = self._create_repo()
        finding = self._create_finding(repo.id, file_path="nonexistent.js", line_number=3)

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/fix")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn("Source file 'nonexistent.js' not found", data["detail"])

    @patch("google.genai.Client")
    def test_successful_fix_generation(self, mock_client_cls):
        repo = self._create_repo()
        source_code = (
            "function computeTotal(items) {\n"
            "  debugger;\n"
            "  return items.reduce((a, b) => a + b, 0);\n"
            "}\n"
        )
        self._create_source_file(repo.id, "src/math.js", source_code)
        finding = self._create_finding(
            repo.id,
            file_path="src/math.js",
            line_number=2,
            rule_id="JS-DEBUGGER",
            severity="warning",
            category="style",
            message="debugger statement found",
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "explanation": "Remove the leftover debugger statement to prevent pausing execution in production.",
            "original_code": "  debugger;\n",
            "fixed_code": "",
            "diff": "--- a/src/math.js\n+++ b/src/math.js\n@@ -2,1 +2,0 @@\n-  debugger;\n",
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/fix")
            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertEqual(data["finding_id"], finding.id)
            self.assertEqual(
                data["explanation"],
                "Remove the leftover debugger statement to prevent pausing execution in production."
            )
            self.assertEqual(data["original_code"], "  debugger;\n")
            self.assertEqual(data["fixed_code"], "")
            self.assertIn("-  debugger;", data["diff"])

    @patch("google.genai.Client")
    def test_prompt_contains_finding_metadata_and_source_context(self, mock_client_cls):
        repo = self._create_repo()
        source_code = "console.log('debug info');\nreturn 42;\n"
        self._create_source_file(repo.id, "src/app.js", source_code)
        finding = self._create_finding(
            repo.id,
            file_path="src/app.js",
            line_number=1,
            rule_id="JS-CONSOLE-LOG",
            severity="info",
            category="style",
            message="Unexpected console.log statement",
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "explanation": "Remove console.log for production readiness.",
            "original_code": "console.log('debug info');",
            "fixed_code": "",
            "diff": None,
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/fix")
            self.assertEqual(response.status_code, 200)

            call_kwargs = mock_instance.models.generate_content.call_args.kwargs
            prompt = call_kwargs["contents"]

            # Verify prompt content
            self.assertIn("JS-CONSOLE-LOG", prompt)
            self.assertIn("info", prompt)
            self.assertIn("style", prompt)
            self.assertIn("Unexpected console.log statement", prompt)
            self.assertIn("src/app.js", prompt)
            self.assertIn("console.log('debug info')", prompt)

            # Check that unified diff was computed when diff was None
            data = response.json()
            self.assertIsNotNone(data["diff"])
            self.assertIn("--- a/src/app.js", data["diff"])

    @patch("google.genai.Client")
    def test_fix_does_not_modify_database_source_file_or_finding(self, mock_client_cls):
        repo = self._create_repo()
        original_content = "def eval_user_input(cmd):\n    eval(cmd)\n"
        sf = self._create_source_file(repo.id, "runner.py", original_content)
        finding = self._create_finding(
            repo.id,
            file_path="runner.py",
            line_number=2,
            rule_id="PY-EVAL",
            severity="critical",
            category="security",
            message="eval call detected",
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "explanation": "Replace eval with ast.literal_eval.",
            "original_code": "    eval(cmd)\n",
            "fixed_code": "    ast.literal_eval(cmd)\n",
            "diff": "--- a/runner.py\n+++ b/runner.py\n@@ -2,1 +2,1 @@\n-    eval(cmd)\n+    ast.literal_eval(cmd)\n",
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/fix")
            self.assertEqual(response.status_code, 200)

        # Query database directly to ensure source_file content was NOT modified
        self.db.refresh(sf)
        self.assertEqual(sf.content, original_content)

        # Ensure finding was NOT modified
        self.db.refresh(finding)
        self.assertEqual(finding.rule_id, "PY-EVAL")
        self.assertEqual(finding.line_number, 2)


if __name__ == "__main__":
    unittest.main()
