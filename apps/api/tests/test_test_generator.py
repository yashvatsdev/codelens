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
from app.schemas.finding import FindingTestResponse
from app.services.test_generator import (
    TestGeneratorError,
    GeminiNotConfiguredError,
    build_test_prompt,
    generate_test,
)


class TestTestGeneratorServiceAndEndpoint(unittest.TestCase):
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
                    Repository.owner == "test-gen-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "test-gen-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "test-gen-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str = "test-repo") -> Repository:
        repo = Repository(
            github_id=f"test-gen-org/{name}",
            name=name,
            full_name=f"test-gen-org/{name}",
            owner="test-gen-org",
            url=f"https://github.com/test-gen-org/{name}",
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
            sha="testgensha1234567890",
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
        file_path: str = "src/calculator.py",
        line_number: int = 4,
        rule_id: str = "PY-DIV-ZERO",
        severity: str = "error",
        category: str = "bug",
        message: str = "Possible division by zero",
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

    def test_missing_api_key_returns_503(self):
        repo = self._create_repo()
        self._create_source_file(repo.id, "app.py", "x = 1\n")
        finding = self._create_finding(repo.id, file_path="app.py", line_number=1)

        with patch.object(settings, "gemini_api_key", None):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/test")
            self.assertEqual(response.status_code, 503)
            data = response.json()
            self.assertIn("Gemini API is not configured", data["detail"])

    def test_missing_repository_returns_404(self):
        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post("/repositories/999999/findings/1/test")
            self.assertEqual(response.status_code, 404)

    def test_missing_finding_returns_404(self):
        repo = self._create_repo()
        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/999999/test")
            self.assertEqual(response.status_code, 404)

    def test_finding_belonging_to_another_repo_returns_404(self):
        repo_a = self._create_repo("repo-a")
        repo_b = self._create_repo("repo-b")
        finding_a = self._create_finding(repo_a.id, file_path="a.py", line_number=1)

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo_b.id}/findings/{finding_a.id}/test")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn(f"Finding with id {finding_a.id} not found for repository {repo_b.id}", data["detail"])

    def test_missing_source_file_returns_404(self):
        repo = self._create_repo()
        finding = self._create_finding(repo.id, file_path="nonexistent.py", line_number=1)

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/test")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn("Source file 'nonexistent.py' not found", data["detail"])

    def test_finding_without_file_path_returns_404(self):
        repo = self._create_repo()
        finding = self._create_finding(repo.id, file_path=None, line_number=1)

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/test")
            self.assertEqual(response.status_code, 404)
            data = response.json()
            self.assertIn("does not specify a file path", data["detail"])

    @patch("google.genai.Client")
    def test_successful_test_generation_python(self, mock_client_cls):
        repo = self._create_repo()
        source_code = (
            "def divide(a, b):\n"
            "    return a / b\n"
        )
        self._create_source_file(repo.id, "src/math_ops.py", source_code)
        finding = self._create_finding(
            repo.id,
            file_path="src/math_ops.py",
            line_number=2,
            rule_id="PY-DIV-ZERO",
            severity="error",
            category="bug",
            message="Possible division by zero",
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "test_framework": "pytest",
            "test_file": "tests/test_math_ops.py",
            "test_code": (
                "import pytest\n"
                "from src.math_ops import divide\n\n"
                "def test_divide_by_zero():\n"
                "    with pytest.raises(ZeroDivisionError):\n"
                "        divide(10, 0)\n"
            ),
            "explanation": "Test verifies that ZeroDivisionError is raised when dividing by zero.",
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/test")
            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertEqual(data["finding_id"], finding.id)
            self.assertEqual(data["test_framework"], "pytest")
            self.assertEqual(data["test_file"], "tests/test_math_ops.py")
            self.assertIn("test_divide_by_zero", data["test_code"])
            self.assertEqual(
                data["explanation"],
                "Test verifies that ZeroDivisionError is raised when dividing by zero."
            )

    @patch("google.genai.Client")
    def test_prompt_contains_metadata_and_context(self, mock_client_cls):
        repo = self._create_repo()
        source_code = "const auth = (token) => eval(token);\n"
        self._create_source_file(repo.id, "src/auth.js", source_code)
        finding = self._create_finding(
            repo.id,
            file_path="src/auth.js",
            line_number=1,
            rule_id="JS-EVAL",
            severity="error",
            category="security",
            message="eval execution vulnerability",
        )

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "test_framework": "jest",
            "test_file": "__tests__/auth.test.js",
            "test_code": "test('rejects eval', () => { expect(1).toBe(1); });",
            "explanation": "Ensures no eval execution occurs.",
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/test")
            self.assertEqual(response.status_code, 200)

            call_kwargs = mock_instance.models.generate_content.call_args.kwargs
            prompt = call_kwargs["contents"]

            self.assertIn("JS-EVAL", prompt)
            self.assertIn("security", prompt)
            self.assertIn("eval execution vulnerability", prompt)
            self.assertIn("src/auth.js", prompt)
            self.assertIn("eval(token)", prompt)

    @patch("google.genai.Client")
    def test_malformed_ai_response_fallback(self, mock_client_cls):
        repo = self._create_repo()
        source_code = "def foo(): pass\n"
        self._create_source_file(repo.id, "app.py", source_code)
        finding = self._create_finding(repo.id, file_path="app.py", line_number=1)

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "def test_raw_fallback(): assert True"
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/test")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["finding_id"], finding.id)
            self.assertEqual(data["test_code"], "def test_raw_fallback(): assert True")
            self.assertEqual(data["test_framework"], "pytest")

    @patch("google.genai.Client")
    def test_does_not_modify_database_or_source_file(self, mock_client_cls):
        repo = self._create_repo()
        orig_content = "def sensitive_calc():\n    return 42\n"
        sf = self._create_source_file(repo.id, "secure.py", orig_content)
        finding = self._create_finding(repo.id, file_path="secure.py", line_number=2)

        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "test_framework": "pytest",
            "test_file": "tests/test_secure.py",
            "test_code": "def test_calc(): assert sensitive_calc() == 42",
            "explanation": "Tests calculate return value.",
        })
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        with patch.object(settings, "gemini_api_key", "mock-key"):
            response = self.client.post(f"/repositories/{repo.id}/findings/{finding.id}/test")
            self.assertEqual(response.status_code, 200)

        self.db.refresh(sf)
        self.assertEqual(sf.content, orig_content)
        self.db.refresh(finding)
        self.assertEqual(finding.line_number, 2)


if __name__ == "__main__":
    unittest.main()

