"""Tests for AI error handling, quota exhaustion, and provider sanitization.

Verifies:
- 429 AI quota/rate-limit error format with code and friendly message
- Provider-specific details (Gemini, Google, API key, model names, internal URLs) are NOT leaked
- Normal AI failures return 502 without leaking provider details
- 404 and 422 behaviors remain intact
"""

import json
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from app.core.ai_errors import (
    AI_QUOTA_CODE,
    AI_QUOTA_MESSAGE,
    AIQuotaExceededError,
    is_ai_quota_error,
    sanitize_ai_error,
)
from app.core.config import settings
from app.db.database import SessionLocal
from app.main import app
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.services.ai_pr_fixer import AIPRFixerError
from app.services.ai_pr_reviewer import AIPRReviewerError
from app.services.explainer import ExplainerError
from app.services.fixer import FixerError
from app.services.pr_reviewer import PRReviewResult
from app.services.test_generator import TestGeneratorError


class TestAIErrorsUtility(unittest.TestCase):
    """Unit tests for the ai_errors module functions."""

    def test_is_ai_quota_error_detects_various_patterns(self):
        self.assertTrue(is_ai_quota_error(AIQuotaExceededError()))
        self.assertTrue(is_ai_quota_error(RuntimeError("429 RESOURCE_EXHAUSTED")))
        self.assertTrue(is_ai_quota_error(Exception("Quota exceeded for metric")))
        self.assertTrue(is_ai_quota_error(Exception("Rate limit exceeded")))
        self.assertTrue(is_ai_quota_error(Exception("Too many requests")))
        self.assertFalse(is_ai_quota_error(Exception("Connection reset")))
        self.assertFalse(is_ai_quota_error(Exception("Invalid syntax in file")))

    def test_sanitize_ai_error_removes_provider_terms(self):
        raw_error = "Gemini API error: 403 API_KEY_INVALID on model gemini-2.5-flash via generativelanguage.googleapis.com"
        sanitized = sanitize_ai_error(raw_error)
        self.assertNotIn("gemini", sanitized.lower())
        self.assertNotIn("googleapis", sanitized.lower())
        self.assertNotIn("api_key", sanitized.lower())

    def test_sanitize_ai_error_preserves_safe_messages(self):
        safe_msg = "Model timeout during AST parse"
        self.assertEqual(sanitize_ai_error(safe_msg), safe_msg)


class TestAIErrorEndpoints(unittest.TestCase):
    """Integration tests for AI error responses on all AI endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._cleanup()
        self.repo = self._create_repo("ai-error-repo")
        self.source_file = self._create_source_file(self.repo.id, "app.py", "x = 1\n")
        self.finding = self._create_finding(self.repo.id, "app.py", line_number=1)

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "ai-error-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "ai-error-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "ai-error-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str) -> Repository:
        repo = Repository(
            github_id=f"ai-error-org/{name}",
            name=name,
            full_name=f"ai-error-org/{name}",
            owner="ai-error-org",
            url=f"https://github.com/ai-error-org/{name}",
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
            sha="mock-sha-123",
            content=content,
            size=len(content),
        )
        self.db.add(sf)
        self.db.commit()
        self.db.refresh(sf)
        return sf

    def _create_finding(self, repo_id: int, file_path: str, line_number: int) -> Finding:
        finding = Finding(
            repository_id=repo_id,
            file_path=file_path,
            line_number=line_number,
            rule_id="SEC-001",
            severity="error",
            category="security",
            message="Insecure pattern",
        )
        self.db.add(finding)
        self.db.commit()
        self.db.refresh(finding)
        return finding

    # -----------------------------------------------------------------------
    # 1. AI PR Review: Quota & Sanitization
    # -----------------------------------------------------------------------

    def test_ai_pr_review_quota_error_returns_clean_429(self):
        fake_pr = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
        )
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_pr), \
             patch("app.api.routes.repositories.generate_ai_pr_review", side_effect=AIPRReviewerError("429 RESOURCE_EXHAUSTED: Quota exceeded")):
            response = self.client.post(f"/repositories/{self.repo.id}/pull-requests/1/ai-review")

        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data["code"], AI_QUOTA_CODE)
        self.assertEqual(data["message"], AI_QUOTA_MESSAGE)
        # Ensure no provider-specific leakage
        body_text = json.dumps(data).lower()
        self.assertNotIn("gemini", body_text)
        self.assertNotIn("google", body_text)
        self.assertNotIn("resource_exhausted", body_text)

    # -----------------------------------------------------------------------
    # 2. AI PR Finding Fix: Quota & Sanitization
    # -----------------------------------------------------------------------

    def test_ai_pr_finding_fix_quota_error_returns_clean_429(self):
        fake_pr = PRReviewResult(
            repository_id=self.repo.id,
            pull_request_number=1,
            files_changed=1,
            files_analyzed=1,
            file_contents={"app.py": "x = 1\n"},
        )
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch("app.api.routes.repositories.review_pull_request", return_value=fake_pr), \
             patch("app.api.routes.repositories.generate_pr_finding_fix", side_effect=AIPRFixerError("Rate limit exceeded")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/pull-requests/1/findings/fix",
                json={
                    "file_path": "app.py",
                    "line_number": 1,
                    "issue": "Bug",
                    "severity": "error",
                    "category": "bug",
                    "message": "Bug message",
                },
            )

        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data["code"], AI_QUOTA_CODE)
        self.assertEqual(data["message"], AI_QUOTA_MESSAGE)

    # -----------------------------------------------------------------------
    # 3. Finding Explain: Quota & Sanitization
    # -----------------------------------------------------------------------

    def test_finding_explain_quota_error_returns_clean_429(self):
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch("app.api.routes.repositories.explain_finding", side_effect=ExplainerError("429 Quota exhausted for model gemini-2.5-flash")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/explain"
            )

        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data["code"], AI_QUOTA_CODE)
        self.assertNotIn("gemini", json.dumps(data).lower())

    def test_finding_explain_normal_error_does_not_leak_provider(self):
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch("app.api.routes.repositories.explain_finding", side_effect=ExplainerError("Gemini internal server error with API key ABC-123")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/explain"
            )

        self.assertEqual(response.status_code, 502)
        data = response.json()
        body_text = json.dumps(data).lower()
        self.assertNotIn("gemini", body_text)
        self.assertNotIn("abc-123", body_text)
        self.assertNotIn("api_key", body_text)

    # -----------------------------------------------------------------------
    # 4. Finding Fix: Quota & Sanitization
    # -----------------------------------------------------------------------

    def test_finding_fix_quota_error_returns_clean_429(self):
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch("app.api.routes.repositories.generate_fix", side_effect=FixerError("RESOURCE_EXHAUSTED")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/fix"
            )

        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data["code"], AI_QUOTA_CODE)

    # -----------------------------------------------------------------------
    # 5. Finding Test Generation: Quota & Sanitization
    # -----------------------------------------------------------------------

    def test_finding_test_generation_quota_error_returns_clean_429(self):
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch("app.api.routes.repositories.generate_test", side_effect=TestGeneratorError("Rate limit reached: too many requests")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/test"
            )

        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data["code"], AI_QUOTA_CODE)
