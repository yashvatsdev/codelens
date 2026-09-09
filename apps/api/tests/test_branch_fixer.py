"""Tests for the GitHub Apply AI Fix to Branch milestone.

Verifies:
- successful branch creation from default branch HEAD
- successful file commit to the new branch
- correct modified file content
- default branch remains untouched
- repository and finding validation
- rejection of unchanged AI fixes
- missing GitHub credentials (503)
- GitHub 404 handling
- GitHub rate limit handling (429)
- GitHub API failure handling (502)
- AI fix failure handling
- zero database mutations
- OpenAPI documentation endpoint presence
"""

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from app.core.ai_errors import AI_QUOTA_CODE, AIQuotaExceededError
from app.core.config import settings
from app.db.database import SessionLocal
from app.main import app
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.schemas.finding import (
    ApplyFixBranchResponse,
    CreatePRFromBranchRequest,
    CreatePRFromBranchResponse,
    FindingFixResponse,
)
from app.services.branch_fixer import (
    BranchCommitFailedError,
    BranchFixerError,
    BranchNotFoundError,
    GitHubCredentialsUnavailableError,
    PullRequestAlreadyExistsError,
    UnchangedFixError,
    apply_ai_fix_to_github_branch,
    commit_file_to_branch,
    create_git_branch,
    create_pr_from_fix_branch,
    get_branch_head_sha,
    get_file_sha_on_branch,
    get_repository_default_branch,
)
from app.services.fixer import FixerError
from app.services.github import (
    GitHubAPIError,
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
)


class TestBranchFixerService(unittest.TestCase):
    """Unit tests for branch_fixer service operations."""

    def test_apply_ai_fix_success(self):
        """AI fix is applied, branch is created from default branch HEAD, and file is committed."""
        repo = Repository(
            id=10,
            github_id="owner/repo",
            name="repo",
            full_name="owner/repo",
            owner="owner",
            url="https://github.com/owner/repo",
            default_branch="main",
        )
        finding = Finding(
            id=123,
            repository_id=10,
            file_path="app.py",
            line_number=1,
            rule_id="SEC-001",
            severity="error",
            category="security",
            message="eval used",
        )
        source_file = SourceFile(
            id=1,
            repository_id=10,
            path="app.py",
            sha="orig-sha-111",
            content="eval(user_input)\n",
            size=17,
        )

        fake_fix = FindingFixResponse(
            finding_id=123,
            explanation="Use literal_eval instead",
            original_code="eval(user_input)",
            fixed_code="ast.literal_eval(user_input)",
            diff=None,
            resulting_code="ast.literal_eval(user_input)\n",
        )

        api_calls = []

        def fake_github_request(url, method="GET", data=None, token=None, timeout=15):
            api_calls.append({"url": url, "method": method, "data": data})
            if "repos/owner/repo/git/ref/heads/main" in url:
                return {"object": {"sha": "default-head-sha-999"}}
            if url == "https://api.github.com/repos/owner/repo":
                return {"default_branch": "main"}
            if "repos/owner/repo/git/refs" in url and method == "POST":
                return {"ref": data["ref"], "object": {"sha": data["sha"]}}
            if "contents/app.py?ref=" in url and method == "GET":
                return {"sha": "orig-blob-sha-222"}
            if "contents/app.py" in url and method == "PUT":
                return {
                    "commit": {
                        "sha": "new-commit-sha-777",
                        "html_url": "https://github.com/owner/repo/commit/new-commit-sha-777",
                    }
                }
            return {}

        with patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_github_request):
            result = apply_ai_fix_to_github_branch(
                repository=repo,
                finding=finding,
                source_file=source_file,
                token="mock-token-xyz",
            )

        self.assertEqual(result["repository_id"], 10)
        self.assertEqual(result["finding_id"], 123)
        self.assertTrue(result["branch_name"].startswith("codelens/fix/finding-123-"))
        self.assertEqual(result["commit_sha"], "new-commit-sha-777")
        self.assertEqual(
            result["commit_url"],
            "https://github.com/owner/repo/commit/new-commit-sha-777",
        )
        self.assertEqual(result["file_path"], "app.py")
        self.assertEqual(result["message"], "AI fix applied successfully to GitHub branch.")

        # Verify default branch was NEVER modified
        for call in api_calls:
            if call["method"] in ("POST", "PUT", "PATCH", "DELETE"):
                data_str = json.dumps(call.get("data") or {})
                self.assertNotIn("refs/heads/main", data_str)
                if call["method"] == "PUT":
                    self.assertNotEqual(call["data"].get("branch"), "main")
                    # Verify content was encoded and modified
                    encoded_content = call["data"]["content"]
                    decoded = base64.b64decode(encoded_content).decode("utf-8")
                    self.assertIn("ast.literal_eval(user_input)", decoded)
                    self.assertTrue(decoded.startswith("ast.literal_eval("))

    def test_unchanged_fix_raises_unchanged_error(self):
        """If AI fix does not change the file content, UnchangedFixError is raised without GitHub calls."""
        repo = Repository(id=1, owner="o", name="r", full_name="o/r", default_branch="main")
        finding = Finding(id=1, repository_id=1, file_path="app.py", line_number=1)
        source_file = SourceFile(id=1, repository_id=1, path="app.py", content="x = 1\n", sha="s1", size=6)

        # Fix where original and fixed code are identical
        fake_fix = FindingFixResponse(
            finding_id=1,
            explanation="No change",
            original_code="x = 1",
            fixed_code="x = 1",
        )

        with patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request") as mock_req:
            with self.assertRaises(UnchangedFixError):
                apply_ai_fix_to_github_branch(
                    repository=repo,
                    finding=finding,
                    source_file=source_file,
                    token="token",
                )
            mock_req.assert_not_called()

    def test_missing_github_token_raises_credentials_unavailable(self):
        """Missing token raises GitHubCredentialsUnavailableError."""
        repo = Repository(id=1, owner="o", name="r", full_name="o/r", default_branch="main")
        finding = Finding(id=1, repository_id=1, file_path="app.py", line_number=1)
        source_file = SourceFile(id=1, repository_id=1, path="app.py", content="x = 1\n", sha="s", size=6)

        with patch.object(settings, "github_token", None):
            with self.assertRaises(GitHubCredentialsUnavailableError):
                apply_ai_fix_to_github_branch(
                    repository=repo,
                    finding=finding,
                    source_file=source_file,
                    token=None,
                )

    def test_branch_commit_failure_raises_branch_commit_failed_error(self):
        """If branch creation succeeds but committing the file fails, BranchCommitFailedError is raised."""
        repo = Repository(id=1, owner="o", name="r", full_name="o/r", default_branch="main")
        finding = Finding(id=1, repository_id=1, file_path="app.py", line_number=1)
        source_file = SourceFile(id=1, repository_id=1, path="app.py", content="eval(x)\n", sha="s", size=8)

        fake_fix = FindingFixResponse(
            finding_id=1,
            explanation="Fix",
            original_code="eval(x)",
            fixed_code="int(x)",
        )

        def fake_req(url, method="GET", data=None, token=None, timeout=15):
            if url == "https://api.github.com/repos/o/r":
                return {"default_branch": "main"}
            if "git/ref/heads/main" in url:
                return {"object": {"sha": "sha-main"}}
            if "git/refs" in url and method == "POST":
                return {"ref": data["ref"]}
            if "contents/app.py?ref=" in url:
                return {"sha": "orig-sha"}
            if "contents/app.py" in url and method == "PUT":
                raise GitHubAPIError("GitHub 500 error during commit write")
            return {}

        with patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_req):
            with self.assertRaises(BranchCommitFailedError) as cm:
                apply_ai_fix_to_github_branch(
                    repository=repo,
                    finding=finding,
                    source_file=source_file,
                    token="token",
                )
            self.assertIn("Branch 'codelens/fix/finding-1-", str(cm.exception))
            self.assertIn("committing the fix failed", str(cm.exception))


class TestApplyFixBranchEndpoint(unittest.TestCase):
    """Integration tests for POST /repositories/{id}/findings/{id}/apply-fix."""

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._cleanup()
        self.repo = self._create_repo("test-branch-fix-repo")
        self.source_file = self._create_source_file(
            self.repo.id,
            "app.py",
            "import os\neval(user_input)\n",
        )
        self.finding = self._create_finding(self.repo.id, "app.py", line_number=2)

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "test-branch-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "test-branch-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "test-branch-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str) -> Repository:
        repo = Repository(
            github_id=f"test-branch-org/{name}",
            name=name,
            full_name=f"test-branch-org/{name}",
            owner="test-branch-org",
            url=f"https://github.com/test-branch-org/{name}",
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
            sha="sha-app-py-123",
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
            message="Avoid eval",
        )
        self.db.add(finding)
        self.db.commit()
        self.db.refresh(finding)
        return finding

    def test_apply_fix_endpoint_success(self):
        """Successful endpoint invocation returns 200 with ApplyFixBranchResponse."""
        fake_fix = FindingFixResponse(
            finding_id=self.finding.id,
            explanation="Replaced eval with literal_eval",
            original_code="eval(user_input)",
            fixed_code="ast.literal_eval(user_input)",
            diff=None,
        )

        def fake_gh_req(url, method="GET", data=None, token=None, timeout=15):
            if url == "https://api.github.com/repos/test-branch-org/test-branch-fix-repo":
                return {"default_branch": "main"}
            if "git/ref/heads/main" in url:
                return {"object": {"sha": "head-sha-1111"}}
            if "git/refs" in url and method == "POST":
                return {"ref": data["ref"], "object": {"sha": data["sha"]}}
            if "contents/app.py?ref=" in url:
                return {"sha": "file-blob-sha-2222"}
            if "contents/app.py" in url and method == "PUT":
                return {
                    "commit": {
                        "sha": "commit-sha-9999",
                        "html_url": "https://github.com/test-branch-org/test-branch-fix-repo/commit/commit-sha-9999",
                    }
                }
            return {}

        with patch.object(settings, "gemini_api_key", "mock-gemini-key"), \
             patch.object(settings, "github_token", "mock-github-token"), \
             patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_gh_req):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["repository_id"], self.repo.id)
        self.assertEqual(data["finding_id"], self.finding.id)
        self.assertTrue(data["branch_name"].startswith(f"codelens/fix/finding-{self.finding.id}-"))
        self.assertEqual(data["commit_sha"], "commit-sha-9999")
        self.assertEqual(
            data["commit_url"],
            "https://github.com/test-branch-org/test-branch-fix-repo/commit/commit-sha-9999",
        )
        self.assertEqual(data["file_path"], "app.py")
        self.assertEqual(data["message"], "AI fix applied successfully to GitHub branch.")

        # Verify DB records were NOT modified
        refreshed_sf = self.db.get(SourceFile, self.source_file.id)
        self.assertIn("eval(user_input)", refreshed_sf.content)

    def test_apply_fix_custom_branch_and_commit_message(self):
        """Supports optional custom branch name and commit message."""
        fake_fix = FindingFixResponse(
            finding_id=self.finding.id,
            explanation="Fix",
            original_code="eval(user_input)",
            fixed_code="ast.literal_eval(user_input)",
        )

        recorded_branch = None
        recorded_msg = None

        def fake_gh_req(url, method="GET", data=None, token=None, timeout=15):
            nonlocal recorded_branch, recorded_msg
            if url.endswith("/repos/test-branch-org/test-branch-fix-repo"):
                return {"default_branch": "main"}
            if "git/ref/heads/main" in url:
                return {"object": {"sha": "head-sha"}}
            if "git/refs" in url and method == "POST":
                recorded_branch = data["ref"]
                return {"ref": data["ref"]}
            if "contents/app.py?ref=" in url:
                return {"sha": "orig-sha"}
            if "contents/app.py" in url and method == "PUT":
                recorded_msg = data["message"]
                return {
                    "commit": {
                        "sha": "c-sha",
                        "html_url": "https://github.com/commit/c-sha",
                    }
                }
            return {}

        with patch.object(settings, "gemini_api_key", "mock-gemini-key"), \
             patch.object(settings, "github_token", "mock-github-token"), \
             patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_gh_req):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={
                    "commit_message": "custom: fix eval issue",
                    "branch_name": "custom-security-fix",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(recorded_branch, "refs/heads/custom-security-fix")
        self.assertEqual(recorded_msg, "custom: fix eval issue")

    def test_repository_not_found(self):
        """Invalid repository returns 404."""
        response = self.client.post(
            f"/repositories/999999/findings/{self.finding.id}/apply-fix",
            json={},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Repository with id 999999 not found", response.json()["detail"])

    def test_finding_not_found(self):
        """Invalid finding returns 404."""
        response = self.client.post(
            f"/repositories/{self.repo.id}/findings/999999/apply-fix",
            json={},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Finding with id 999999 not found", response.json()["detail"])

    def test_finding_belongs_to_other_repository(self):
        """Finding belonging to a different repository returns 404."""
        other_repo = self._create_repo("other-repo")
        response = self.client.post(
            f"/repositories/{other_repo.id}/findings/{self.finding.id}/apply-fix",
            json={},
        )
        self.assertEqual(response.status_code, 404)

    def test_source_file_not_found(self):
        """Missing source file returns 404."""
        missing_finding = Finding(
            repository_id=self.repo.id,
            file_path="missing_file.py",
            line_number=1,
            rule_id="SEC-001",
            severity="error",
            category="security",
            message="No file",
        )
        self.db.add(missing_finding)
        self.db.commit()
        self.db.refresh(missing_finding)

        response = self.client.post(
            f"/repositories/{self.repo.id}/findings/{missing_finding.id}/apply-fix",
            json={},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Source file 'missing_file.py' not found", response.json()["detail"])

    def test_missing_gemini_api_key_returns_503(self):
        """Missing GEMINI_API_KEY returns 503."""
        with patch.object(settings, "gemini_api_key", None), \
             patch.object(settings, "github_token", "mock-token"):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Gemini API is not configured", response.json()["detail"])

    def test_missing_github_token_returns_503(self):
        """Missing GITHUB_TOKEN returns 503."""
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", None):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("GitHub credentials are unavailable", response.json()["detail"])

    def test_unchanged_fix_rejected_with_400(self):
        """Unchanged file content after fix returns 400 Bad Request."""
        fake_fix = FindingFixResponse(
            finding_id=self.finding.id,
            explanation="No change",
            original_code="nonexistent_pattern",
            fixed_code="replacement",
        )

        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer.generate_fix", return_value=fake_fix):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("did not change the source file content", response.json()["detail"])

    def test_github_404_handled_with_404(self):
        """GitHub 404 returns 404."""
        fake_fix = FindingFixResponse(
            finding_id=self.finding.id,
            explanation="Fix",
            original_code="eval(user_input)",
            fixed_code="literal_eval(user_input)",
        )

        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=GitHubRepoNotFoundError("GitHub repo not found")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 404)

    def test_github_rate_limit_handled_with_429(self):
        """GitHub rate limit returns 429."""
        fake_fix = FindingFixResponse(
            finding_id=self.finding.id,
            explanation="Fix",
            original_code="eval(user_input)",
            fixed_code="literal_eval(user_input)",
        )

        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=GitHubRateLimitError("Rate limit")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 429)

    def test_github_api_failure_handled_with_502(self):
        """GitHub API failure returns 502."""
        fake_fix = FindingFixResponse(
            finding_id=self.finding.id,
            explanation="Fix",
            original_code="eval(user_input)",
            fixed_code="literal_eval(user_input)",
        )

        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=GitHubAPIError("GitHub server error")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 502)

    def test_branch_created_commit_failed_returns_502_with_explanation(self):
        """Branch created but commit failed returns 502 explaining branch was created."""
        fake_fix = FindingFixResponse(
            finding_id=self.finding.id,
            explanation="Fix",
            original_code="eval(user_input)",
            fixed_code="literal_eval(user_input)",
        )

        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer.generate_fix", return_value=fake_fix), \
             patch("app.api.routes.repositories.apply_ai_fix_to_github_branch", side_effect=BranchCommitFailedError("branch-123", "Write timeout")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Branch 'branch-123' was created, but committing the fix failed", response.json()["detail"])

    def test_ai_fix_failure_handled_without_leaking_provider(self):
        """AI generation error returns 502 without provider details."""
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer.generate_fix", side_effect=FixerError("Gemini internal error on gemini-2.5-flash with key 1234")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"].lower()
        self.assertNotIn("gemini", detail)
        self.assertNotIn("1234", detail)

    def test_ai_quota_exceeded_returns_429(self):
        """AI quota error returns 429 with AI_QUOTA_EXCEEDED."""
        with patch.object(settings, "gemini_api_key", "mock-key"), \
             patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer.generate_fix", side_effect=AIQuotaExceededError()):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/apply-fix",
                json={},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], AI_QUOTA_CODE)

    def test_openapi_spec_includes_apply_fix_endpoint(self):
        """OpenAPI schema contains the apply-fix endpoint."""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        openapi = response.json()
        path = "/repositories/{repository_id}/findings/{finding_id}/apply-fix"
        self.assertIn(path, openapi["paths"])
        self.assertIn("post", openapi["paths"][path])


class TestCreatePRFromBranchEndpoint(unittest.TestCase):
    """Integration tests for POST /repositories/{id}/findings/{id}/create-pr."""

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._cleanup()
        self.repo = self._create_repo("test-pr-create-repo")
        self.source_file = self._create_source_file(
            self.repo.id,
            "app.py",
            "x = 1\n",
        )
        self.finding = self._create_finding(self.repo.id, "app.py", line_number=1)

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "test-pr-create-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "test-pr-create-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "test-pr-create-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self, name: str) -> Repository:
        repo = Repository(
            github_id=f"test-pr-create-org/{name}",
            name=name,
            full_name=f"test-pr-create-org/{name}",
            owner="test-pr-create-org",
            url=f"https://github.com/test-pr-create-org/{name}",
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
            sha="sha-app-py",
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
            message="Avoid eval",
        )
        self.db.add(finding)
        self.db.commit()
        self.db.refresh(finding)
        return finding

    def test_create_pr_success_with_defaults(self):
        """Creates a Pull Request with default title and body."""
        posted_payload = None

        def fake_gh_req(url, method="GET", data=None, token=None, timeout=15):
            nonlocal posted_payload
            if "git/ref/heads/codelens/fix/finding-1-abc" in url:
                return {"object": {"sha": "branch-head-sha"}}
            if url.endswith("/repos/test-pr-create-org/test-pr-create-repo"):
                return {"default_branch": "main"}
            if url.endswith("/repos/test-pr-create-org/test-pr-create-repo/pulls") and method == "POST":
                posted_payload = data
                return {
                    "number": 42,
                    "html_url": "https://github.com/test-pr-create-org/test-pr-create-repo/pull/42",
                    "title": data.get("title"),
                    "state": "open",
                }
            return {}

        with patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_gh_req):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={"branch_name": "codelens/fix/finding-1-abc"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["repository_id"], self.repo.id)
        self.assertEqual(data["finding_id"], self.finding.id)
        self.assertEqual(data["pull_request_number"], 42)
        self.assertEqual(
            data["pull_request_url"],
            "https://github.com/test-pr-create-org/test-pr-create-repo/pull/42",
        )
        self.assertEqual(data["branch_name"], "codelens/fix/finding-1-abc")
        self.assertEqual(data["base_branch"], "main")
        self.assertEqual(data["message"], "Pull Request created successfully.")

        # Check posted payload to GitHub
        self.assertIsNotNone(posted_payload)
        self.assertEqual(posted_payload["head"], "codelens/fix/finding-1-abc")
        self.assertEqual(posted_payload["base"], "main")
        self.assertIn("fix: resolve SEC-001 in app.py", posted_payload["title"])
        self.assertIn("CodeLens AI Proposed Fix", posted_payload["body"])

    def test_create_pr_custom_title_and_body(self):
        """Creates a Pull Request with custom title and body."""
        posted_payload = None

        def fake_gh_req(url, method="GET", data=None, token=None, timeout=15):
            nonlocal posted_payload
            if "git/ref/heads/my-fix-branch" in url:
                return {"object": {"sha": "sha-head"}}
            if url.endswith("/repos/test-pr-create-org/test-pr-create-repo"):
                return {"default_branch": "main"}
            if url.endswith("/repos/test-pr-create-org/test-pr-create-repo/pulls") and method == "POST":
                posted_payload = data
                return {
                    "number": 101,
                    "html_url": "https://github.com/pull/101",
                    "title": data.get("title"),
                }
            return {}

        with patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_gh_req):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={
                    "branch_name": "my-fix-branch",
                    "title": "fix: custom security patch",
                    "body": "Detailed custom PR description.",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(posted_payload["title"], "fix: custom security patch")
        self.assertEqual(posted_payload["body"], "Detailed custom PR description.")
        self.assertEqual(posted_payload["head"], "my-fix-branch")
        self.assertEqual(posted_payload["base"], "main")

    def test_create_pr_repository_not_found(self):
        """Non-existent repository returns 404."""
        response = self.client.post(
            f"/repositories/999999/findings/{self.finding.id}/create-pr",
            json={"branch_name": "some-branch"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Repository with id 999999 not found", response.json()["detail"])

    def test_create_pr_finding_not_found(self):
        """Non-existent finding returns 404."""
        response = self.client.post(
            f"/repositories/{self.repo.id}/findings/999999/create-pr",
            json={"branch_name": "some-branch"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Finding with id 999999 not found", response.json()["detail"])

    def test_create_pr_finding_belongs_to_other_repository(self):
        """Finding belonging to another repository returns 404."""
        other_repo = self._create_repo("other-repo-2")
        response = self.client.post(
            f"/repositories/{other_repo.id}/findings/{self.finding.id}/create-pr",
            json={"branch_name": "some-branch"},
        )
        self.assertEqual(response.status_code, 404)

    def test_create_pr_missing_branch_name(self):
        """Missing or empty branch_name returns 422."""
        response1 = self.client.post(
            f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
            json={},
        )
        self.assertEqual(response1.status_code, 422)

        response2 = self.client.post(
            f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
            json={"branch_name": "   "},
        )
        self.assertEqual(response2.status_code, 422)

    def test_create_pr_branch_not_found_on_github(self):
        """Branch not found on GitHub returns 404."""
        with patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=GitHubRepoNotFoundError("Branch not found")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={"branch_name": "nonexistent-branch"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertIn("was not found", response.json()["detail"])

    def test_create_pr_duplicate_conflict_409(self):
        """Duplicate/already existing PR returns 409 Conflict."""
        def fake_gh_req(url, method="GET", data=None, token=None, timeout=15):
            if "git/ref/heads/my-branch" in url:
                return {"object": {"sha": "head-sha"}}
            if url.endswith("/repos/test-pr-create-org/test-pr-create-repo"):
                return {"default_branch": "main"}
            if method == "POST" and url.endswith("/pulls"):
                raise PullRequestAlreadyExistsError("A pull request for branch 'my-branch' already exists.")
            return {}

        with patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_gh_req):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={"branch_name": "my-branch"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("already exists", response.json()["detail"])

    def test_create_pr_missing_github_token_503(self):
        """Missing GITHUB_TOKEN returns 503."""
        with patch.object(settings, "github_token", None):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={"branch_name": "my-branch"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("GitHub credentials are unavailable", response.json()["detail"])

    def test_create_pr_github_rate_limit_429(self):
        """GitHub rate limit returns 429."""
        with patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=GitHubRateLimitError("Rate limit exceeded")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={"branch_name": "my-branch"},
            )
        self.assertEqual(response.status_code, 429)

    def test_create_pr_github_api_failure_502(self):
        """GitHub API failure returns 502."""
        with patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=GitHubAPIError("Network drop")):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={"branch_name": "my-branch"},
            )
        self.assertEqual(response.status_code, 502)

    def test_openapi_spec_includes_create_pr_endpoint(self):
        """OpenAPI schema contains the create-pr endpoint."""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        openapi = response.json()
        path = "/repositories/{repository_id}/findings/{finding_id}/create-pr"
        self.assertIn(path, openapi["paths"])
        self.assertIn("post", openapi["paths"][path])

    def test_default_branch_never_modified_during_pr_creation(self):
        """Verifies default branch is only used as base, never modified."""
        captured_methods = []

        def fake_gh_req(url, method="GET", data=None, token=None, timeout=15):
            captured_methods.append((method, url, data))
            if "git/ref/heads/safe-branch" in url:
                return {"object": {"sha": "sha-head"}}
            if url.endswith("/repos/test-pr-create-org/test-pr-create-repo"):
                return {"default_branch": "main"}
            if method == "POST" and url.endswith("/pulls"):
                return {
                    "number": 99,
                    "html_url": "https://github.com/pull/99",
                    "title": data.get("title"),
                }
            return {}

        with patch.object(settings, "github_token", "mock-token"), \
             patch("app.services.branch_fixer._authenticated_github_request", side_effect=fake_gh_req):
            response = self.client.post(
                f"/repositories/{self.repo.id}/findings/{self.finding.id}/create-pr",
                json={"branch_name": "safe-branch"},
            )

        self.assertEqual(response.status_code, 200)
        for method, url, data in captured_methods:
            if method in ("PUT", "DELETE", "PATCH"):
                self.fail(f"Unexpected mutating call: {method} {url}")
            if method == "POST" and url.endswith("/pulls"):
                self.assertEqual(data["head"], "safe-branch")
                self.assertEqual(data["base"], "main")

