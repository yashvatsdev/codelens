"""GitHub branch creation and AI fix commit service for CodeLens.

Applies an AI-generated fix to a source file, creates a unique GitHub branch
from the repository's default branch HEAD, and commits the modified file to that branch.

SAFETY CONSTRAINTS:
- The default/main branch is NEVER modified.
- No merges or pull requests are created.
- The local CodeLens repository is NEVER modified.
- Database records are NOT modified.
- All GitHub mutations are explicitly triggered by the apply-fix endpoint.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
import uuid
from typing import Any

from app.core.config import settings
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.services.fixer import apply_fix_to_content, generate_fix
from app.services.github import (
    GitHubAPIError,
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
    GitHubServiceError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BranchFixerError(Exception):
    """Base exception for branch fixer errors."""
    pass


class GitHubCredentialsUnavailableError(BranchFixerError):
    """Raised when GitHub token/credentials are missing or unauthorized."""
    pass


class UnchangedFixError(BranchFixerError):
    """Raised when the AI fix results in identical content (no changes)."""
    pass


class BranchCommitFailedError(BranchFixerError):
    """Raised when branch creation succeeds but committing the file fails."""

    def __init__(self, branch_name: str, reason: str):
        super().__init__(
            f"Branch '{branch_name}' was created, but committing the fix failed: {reason}"
        )
        self.branch_name = branch_name
        self.reason = reason


# ---------------------------------------------------------------------------
# Authenticated GitHub HTTP Request Helper
# ---------------------------------------------------------------------------

def _authenticated_github_request(
    url: str,
    method: str = "GET",
    data: dict | None = None,
    token: str | None = None,
    timeout: int = 15,
) -> dict | list:
    """Make an authenticated request to the GitHub REST API.

    Never exposes credentials or sensitive token information in logs or exceptions.
    """
    headers = {
        "User-Agent": "CodeLens-App",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise GitHubCredentialsUnavailableError(
                "GitHub authentication failed (invalid or expired GITHUB_TOKEN)"
            ) from err
        if err.code == 403:
            rate_remaining = err.headers.get("x-ratelimit-remaining")
            if rate_remaining == "0" or "rate limit" in str(err.reason).lower():
                raise GitHubRateLimitError(
                    "GitHub API rate limit exceeded. Please try again later."
                ) from err
            raise GitHubCredentialsUnavailableError(
                f"GitHub permission denied: {err.reason}"
            ) from err
        if err.code == 404:
            raise GitHubRepoNotFoundError(
                f"GitHub resource not found: {url}"
            ) from err
        raise GitHubAPIError(
            f"GitHub API returned error {err.code}: {err.reason}"
        ) from err
    except urllib.error.URLError as err:
        raise GitHubAPIError(
            f"Failed to reach GitHub API: {err.reason}"
        ) from err
    except TimeoutError as err:
        raise GitHubAPIError(
            "Request to GitHub API timed out."
        ) from err


# ---------------------------------------------------------------------------
# GitHub REST API Operations
# ---------------------------------------------------------------------------

def get_repository_default_branch(owner: str, repo: str, token: str) -> str:
    """Fetch the repository's current default branch from GitHub."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    payload = _authenticated_github_request(url, method="GET", token=token)
    if isinstance(payload, dict):
        return payload.get("default_branch") or "main"
    return "main"


def get_branch_head_sha(owner: str, repo: str, branch: str, token: str) -> str:
    """Fetch the commit SHA of a branch HEAD from GitHub."""
    url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{branch}"
    try:
        payload = _authenticated_github_request(url, method="GET", token=token)
        if isinstance(payload, dict):
            sha = payload.get("object", {}).get("sha")
            if sha:
                return sha
    except GitHubRepoNotFoundError:
        pass

    branch_url = f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"
    payload = _authenticated_github_request(branch_url, method="GET", token=token)
    if isinstance(payload, dict):
        sha = payload.get("commit", {}).get("sha")
        if sha:
            return sha

    raise GitHubRepoNotFoundError(
        f"Branch '{branch}' was not found for repository '{owner}/{repo}'"
    )


def create_git_branch(
    owner: str,
    repo: str,
    branch_name: str,
    head_sha: str,
    token: str,
) -> dict:
    """Create a new branch from a given commit SHA via the Git References API.

    POST /repos/{owner}/{repo}/git/refs
    ref must be 'refs/heads/{branch_name}'
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/git/refs"
    data = {
        "ref": f"refs/heads/{branch_name}",
        "sha": head_sha,
    }
    return _authenticated_github_request(url, method="POST", data=data, token=token)


def get_file_sha_on_branch(
    owner: str,
    repo: str,
    path: str,
    branch: str,
    token: str,
) -> str | None:
    """Retrieve the current blob SHA of a file on a branch if it exists.

    Returns None if the file does not exist yet.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    try:
        payload = _authenticated_github_request(url, method="GET", token=token)
        if isinstance(payload, dict):
            return payload.get("sha")
    except GitHubRepoNotFoundError:
        return None
    return None


def commit_file_to_branch(
    owner: str,
    repo: str,
    path: str,
    content: str,
    branch: str,
    commit_message: str,
    file_sha: str | None = None,
    token: str | None = None,
) -> dict:
    """Commit modified file content directly to a specific branch via the GitHub Contents API.

    PUT /repos/{owner}/{repo}/contents/{path}
    File content is Base64 encoded.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    b64_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    data: dict[str, Any] = {
        "message": commit_message,
        "content": b64_content,
        "branch": branch,
    }
    if file_sha:
        data["sha"] = file_sha

    return _authenticated_github_request(url, method="PUT", data=data, token=token)


# ---------------------------------------------------------------------------
# Core Service Coordination
# ---------------------------------------------------------------------------

def apply_ai_fix_to_github_branch(
    repository: Repository,
    finding: Finding,
    source_file: SourceFile,
    commit_message: str | None = None,
    branch_name: str | None = None,
    token: str | None = None,
    gemini_client: Any = None,
) -> dict:
    """Coordinate generating AI fix, creating a unique branch, and committing changes.

    Steps:
    1. Validate GitHub credentials.
    2. Generate AI fix using existing fixer service.
    3. Apply fix in memory to source content.
    4. If content is unchanged, abort with UnchangedFixError.
    5. Fetch repository's current default branch.
    6. Fetch default branch HEAD commit SHA.
    7. Generate unique branch name (codelens/fix/finding-{id}-{short_id}).
    8. Create branch from default branch HEAD.
    9. Commit modified file to the new branch (safely never touching default branch).
    10. Return branch and commit metadata.
    """
    auth_token = token or settings.github_token
    if not auth_token:
        raise GitHubCredentialsUnavailableError(
            "GitHub credentials are unavailable (missing GITHUB_TOKEN)"
        )

    # 1. Generate AI fix
    fix_result = generate_fix(
        finding=finding,
        source_content=source_file.content,
        gemini_client=gemini_client,
    )

    # 2. Apply fix in memory
    applied_content = apply_fix_to_content(
        source_content=source_file.content,
        original_code=fix_result.original_code,
        fixed_code=fix_result.fixed_code,
        line_number=finding.line_number,
    )

    # 3. Reject unchanged fixes
    if applied_content == source_file.content:
        raise UnchangedFixError(
            "AI fix did not change the source file content; branch was not created."
        )

    # 4. Fetch current default branch
    default_branch = get_repository_default_branch(
        owner=repository.owner,
        repo=repository.name,
        token=auth_token,
    )

    # 5. Fetch default branch HEAD SHA
    head_sha = get_branch_head_sha(
        owner=repository.owner,
        repo=repository.name,
        branch=default_branch,
        token=auth_token,
    )

    # 6. Generate branch name
    if branch_name and branch_name.strip():
        target_branch = branch_name.strip()
    else:
        short_id = uuid.uuid4().hex[:6]
        target_branch = f"codelens/fix/finding-{finding.id}-{short_id}"

    # 7. Create branch from default branch HEAD
    create_git_branch(
        owner=repository.owner,
        repo=repository.name,
        branch_name=target_branch,
        head_sha=head_sha,
        token=auth_token,
    )

    # 8. Commit modified file to the new branch
    file_path = finding.file_path or source_file.path
    default_commit_msg = f"fix: apply CodeLens AI fix to {file_path}"
    msg = commit_message.strip() if commit_message and commit_message.strip() else default_commit_msg

    try:
        file_sha = get_file_sha_on_branch(
            owner=repository.owner,
            repo=repository.name,
            path=file_path,
            branch=target_branch,
            token=auth_token,
        ) or source_file.sha or None

        commit_res = commit_file_to_branch(
            owner=repository.owner,
            repo=repository.name,
            path=file_path,
            content=applied_content,
            branch=target_branch,
            commit_message=msg,
            file_sha=file_sha,
            token=auth_token,
        )
    except Exception as err:
        raise BranchCommitFailedError(
            branch_name=target_branch,
            reason=str(err),
        ) from err

    commit_obj = commit_res.get("commit", {}) if isinstance(commit_res, dict) else {}
    commit_sha = commit_obj.get("sha") or commit_res.get("content", {}).get("sha", "")
    commit_url = (
        commit_obj.get("html_url")
        or f"https://github.com/{repository.owner}/{repository.name}/commit/{commit_sha}"
    )

    return {
        "repository_id": repository.id,
        "finding_id": finding.id,
        "branch_name": target_branch,
        "commit_sha": commit_sha,
        "commit_url": commit_url,
        "file_path": file_path,
        "message": "AI fix applied successfully to GitHub branch.",
    }
