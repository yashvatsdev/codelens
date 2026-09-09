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
from app.schemas.finding import FindingExplanationResponse
from app.services.explainer import (
    ExplainerError,
    FindingNotFoundError,
    GeminiNotConfiguredError,
    SourceFileNotFoundError,
    build_explainer_prompt,
    explain_finding,
    extract_code_context,
)


class TestExplainerServiceAndEndpoint(unittest.TestCase):
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
                    Repository.owner == "explainer-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "explainer-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "explainer-test-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str = "test-repo") -> Repository:
        repo = Repository(
            github_id=f"explainer-test-org/{name}",
            name=name,
            full_name=f"explainer-test-org/{name}",
            owner="explainer-test-org",
            url=f"https://github.com/explainer-test-org/{name}",
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
            sha="testhash1234567890",
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
        file_path: str = "src/app.py",
        line_number: int = 12,
        rule_id: str = "SEC-EVAL",
        severity: str = "high",
        category: str = "security",
        message: str = "Use of eval detected",
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

    def test_extract_code_context(self):
        sample_code = "\n".join([f"line_{i} = {i}" for i in range(1, 31)])
        # Line 15 with window=10 should include lines 5 to 25
        context = extract_code_context(sample_code, line_number=15, window=10)
        self.assertIn("->   15 | line_15 = 15", context)
        self.assertIn("5 | line_5 = 5", context)
        self.assertIn("25 | line_25 = 25", context)
        self.assertNotIn("4 | line_4 = 4", context)
        self.assertNotIn("26 | line_26 = 26", context)

    def test_extract_code_context_empty_and_bounds(self):
        self.assertEqual(extract_code_context("", 5), "(empty file)")

        small_code = "a = 1\nb = 2\nc = 3"
        context = extract_code_context(small_code, line_number=1, window=10)
        self.assertIn("->    1 | a = 1", context)
        self.assertIn("3 | c = 3", context)

        # None line number
        context_none = extract_code_context(small_code, line_number=None, window=10)
        self.assertIn("1 | a = 1", context_none)

    def test_missing_api_key_returns_503(self):
        repo = self._create_repo()
        sf = self._create_source_file(repo.id, "main.py", "x = 1\n")
        finding = self._create_finding(repo.id, file_path="main.py", line_number=1)

        with patch.object(settings, "gemini_api_key", None):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/explain")
            self.assertEqual(response.status_code, 503)
            data = response.json()
            self.assertIn("Gemini API is not configured", data["detail"])

    def test_missing_finding_returns_404(self):
        repo = self._create_repo()
        with patch.object(settings, "gemini_api_key", "mock-test-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/999999/explain")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn("not found", data["detail"])

    def test_missing_repository_returns_404(self):
        with patch.object(settings, "gemini_api_key", "mock-test-key"):
            response = self.client.post("/repositories/999999/findings/1/explain")
            self.assertEqual(response.status_code, 404)

    def test_finding_belonging_to_another_repository_returns_404(self):
        repo_a = self._create_repo("repo-a")
        repo_b = self._create_repo("repo-b")
        finding_a = self._create_finding(repo_a.id, file_path="a.py", line_number=1)

        with patch.object(settings, "gemini_api_key", "mock-test-key"):
            # Attempt to explain finding_a through repo_b's endpoint
            response = self.client.post(f"/repositories/{repo_b.id}/findings/{finding_a.id}/explain")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn(f"Finding with id {finding_a.id} not found for repository {repo_b.id}", data["detail"])

    def test_missing_source_file_returns_404(self):
        repo = self._create_repo()
        # Create finding with file_path pointing to a non-existent SourceFile
        finding = self._create_finding(repo.id, file_path="nonexistent.py", line_number=5)

        with patch.object(settings, "gemini_api_key", "mock-test-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/explain")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn("Source file 'nonexistent.py' not found", data["detail"])

    @patch("google.genai.Client")
    def test_successful_explanation(self, mock_client_cls):
        repo = self._create_repo()
        source_code = (
            "import os\n"
            "def run_command(cmd):\n"
            "    eval(cmd)\n"
            "    return True\n"
        )
        self._create_source_file(repo.id, "src/runner.py", source_code)
        finding = self._create_finding(
            repo.id,
            file_path="src/runner.py",
            line_number=3,
            rule_id="JS-EVAL-USAGE",
            severity="critical",
            category="security",
            message="Dangerous eval statement detected",
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "explanation": "Using eval allows arbitrary code execution.",
            "remediation": "Replace eval with a safer parsing alternative or subprocess."
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-test-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/explain")
            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertEqual(data["finding_id"], finding.id)
            self.assertEqual(data["explanation"], "Using eval allows arbitrary code execution.")
            self.assertEqual(
                data["remediation"],
                "Replace eval with a safer parsing alternative or subprocess."
            )

    @patch("google.genai.Client")
    def test_prompt_contains_finding_metadata_and_source_context(self, mock_client_cls):
        repo = self._create_repo()
        source_code = "\n".join([f"# line {i}" for i in range(1, 20)])
        source_code += "\neval('arbitrary code')\n"
        source_code += "\n".join([f"# line {i}" for i in range(21, 35)])

        self._create_source_file(repo.id, "src/core/eval_test.py", source_code)
        finding = self._create_finding(
            repo.id,
            file_path="src/core/eval_test.py",
            line_number=20,
            rule_id="SEC-DYNAMIC-EVAL",
            severity="critical",
            category="security",
            message="Dynamic code execution with eval is forbidden",
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "explanation": "Found dynamic evaluation vulnerability.",
            "remediation": "Do not pass user strings to eval."
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-test-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/explain")
            self.assertEqual(response.status_code, 200)

            # Inspect the prompt passed to generate_content
            self.assertTrue(mock_instance.models.generate_content.called)
            call_kwargs = mock_instance.models.generate_content.call_args.kwargs
            prompt = call_kwargs["contents"]

            # Verify finding metadata
            self.assertIn("SEC-DYNAMIC-EVAL", prompt)
            self.assertIn("critical", prompt)
            self.assertIn("security", prompt)
            self.assertIn("Dynamic code execution with eval is forbidden", prompt)
            self.assertIn("src/core/eval_test.py", prompt)
            self.assertIn("20", prompt)

            # Verify source code context around line 20
            self.assertIn("eval('arbitrary code')", prompt)
            self.assertIn("->   20 |", prompt)


if __name__ == "__main__":
    unittest.main()

