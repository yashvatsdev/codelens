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


class PullRequestAlreadyExistsError(BranchFixerError):
    """Raised when an open PR already exists for the given head branch."""
    pass


class BranchNotFoundError(BranchFixerError):
    """Raised when the specified head branch is not found on GitHub."""
    pass


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
        err_body = ""
        try:
            err_body = err.read().decode("utf-8")
        except Exception:
            pass
        if err.code == 409 or (err.code == 422 and "already exists" in (err_body.lower() + " " + str(err.reason).lower())):
            raise PullRequestAlreadyExistsError(
                "A pull request already exists for this branch."
            ) from err
        detail_msg = f"{err.reason} {err_body}".strip() if err_body else str(err.reason)
        raise GitHubAPIError(
            f"GitHub API returned error {err.code}: {detail_msg}"
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


# ---------------------------------------------------------------------------
# Phase 2: Pull Request Creation from Fix Branch
# ---------------------------------------------------------------------------

def generate_default_pr_title(finding: Finding) -> str:
    """Generate a clean, conventional Pull Request title for a finding fix."""
    path_suffix = f" in {finding.file_path}" if finding.file_path else ""
    return f"fix: resolve {finding.rule_id}{path_suffix}"


def generate_default_pr_body(finding: Finding, branch_name: str) -> str:
    """Generate structured markdown body for the Pull Request."""
    lines = [
        "## 🔍 CodeLens AI Proposed Fix",
        "",
        f"This Pull Request proposes an automated code fix for finding **`{finding.rule_id}`**.",
        "",
        "### Finding Details",
        f"- **File:** `{finding.file_path or 'unknown'}`",
        f"- **Line:** {finding.line_number if finding.line_number is not None else 'N/A'}",
        f"- **Severity:** `{finding.severity.upper()}`",
        f"- **Category:** `{finding.category.capitalize()}`",
        f"- **Issue:** {finding.message}",
        f"- **Branch:** `{branch_name}`",
        "",
        "---",
        "*Created automatically by [CodeLens](https://github.com/yashvatsdev/codelens)*",
    ]
    return "\n".join(lines)


def create_pull_request_on_github(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    token: str,
) -> dict:
    """Create a Pull Request on GitHub via POST /repos/{owner}/{repo}/pulls."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    }
    return _authenticated_github_request(url, method="POST", data=payload, token=token)


def create_pr_from_fix_branch(
    repository: Repository,
    finding: Finding,
    branch_name: str,
    title: str | None = None,
    body: str | None = None,
    token: str | None = None,
) -> dict:
    """Validate head branch, fetch default branch, and create a Pull Request on GitHub.

    Steps:
    1. Validate GitHub credentials.
    2. Verify head branch exists on GitHub.
    3. Determine repository default branch.
    4. Construct PR title and body.
    5. Call GitHub REST API to create the PR.
    6. Return PR metadata.
    """
    auth_token = token or settings.github_token
    if not auth_token:
        raise GitHubCredentialsUnavailableError(
            "GitHub credentials are unavailable (missing GITHUB_TOKEN)"
        )

    # 1. Verify head branch exists on GitHub
    try:
        get_branch_head_sha(
            owner=repository.owner,
            repo=repository.name,
            branch=branch_name,
            token=auth_token,
        )
    except GitHubRepoNotFoundError as err:
        raise BranchNotFoundError(
            f"Branch '{branch_name}' was not found on repository '{repository.full_name}'."
        ) from err

    # 2. Determine repository default branch
    default_branch = get_repository_default_branch(
        owner=repository.owner,
        repo=repository.name,
        token=auth_token,
    )

    # 3. Prepare title and body
    pr_title = title.strip() if title and title.strip() else generate_default_pr_title(finding)
    pr_body = body.strip() if body and body.strip() else generate_default_pr_body(finding, branch_name)

    # 4. Create Pull Request
    pr_data = create_pull_request_on_github(
        owner=repository.owner,
        repo=repository.name,
        head=branch_name,
        base=default_branch,
        title=pr_title,
        body=pr_body,
        token=auth_token,
    )

    pr_number = pr_data.get("number")
    pr_url = pr_data.get("html_url") or f"https://github.com/{repository.owner}/{repository.name}/pull/{pr_number}"

    return {
        "repository_id": repository.id,
        "finding_id": finding.id,
        "pull_request_number": pr_number,
        "pull_request_url": pr_url,
        "branch_name": branch_name,
        "base_branch": default_branch,
        "title": pr_data.get("title") or pr_title,
        "message": "Pull Request created successfully.",
    }

