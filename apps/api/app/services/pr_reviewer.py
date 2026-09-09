"""Pull Request review service.

Fetches the files changed in a GitHub Pull Request, obtains their
PR-version content, runs the existing CodeLens static analyzers,
and returns findings in memory.

Nothing is written to the database.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from app.services.analyzer import analyze_source_file, ANALYZABLE_EXTENSIONS, get_file_extension
from app.services.github import (
    GitHubAPIError,
    GitHubRepoNotFoundError,
    GitHubServiceError,
    MAX_FILE_SIZE_BYTES,
)


class PRNotFoundError(GitHubServiceError):
    """Raised when a pull request is not found or is inaccessible."""
    pass


# File statuses that should be analyzed (file content is available).
_ANALYZABLE_STATUSES: frozenset[str] = frozenset({"added", "modified", "renamed"})

# Maximum number of changed files to process per PR review.
MAX_PR_FILES: int = 300


@dataclass(frozen=True)
class PRChangedFile:
    """A single file changed in a pull request."""
    filename: str
    status: str  # added, modified, removed, renamed, ...
    patch: str | None = None
    contents_url: str | None = None
    previous_filename: str | None = None


@dataclass(frozen=True)
class PRFinding:
    """A single static analysis finding from a PR review (no database ID)."""
    file_path: str
    line_number: int | None
    severity: str
    category: str
    message: str
    rule_id: str


@dataclass
class PRReviewResult:
    """Summary of a PR review."""
    repository_id: int
    pull_request_number: int
    files_changed: int
    files_analyzed: int
    findings: list[PRFinding] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _github_api_request(url: str, timeout: int = 15) -> dict | list:
    """Make a GET request to the GitHub API and return parsed JSON.

    Reuses the same User-Agent and Accept headers as the existing
    GitHub service, and maps HTTP errors to the same exception hierarchy.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CodeLens-App",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise PRNotFoundError(
                f"GitHub resource not found: {url}"
            ) from err
        if err.code == 403:
            from app.services.github import GitHubRateLimitError
            raise GitHubRateLimitError(
                "GitHub API rate limit exceeded. Please try again later."
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


def _fetch_pr_metadata(owner: str, repo: str, pr_number: int) -> dict:
    """Fetch basic PR metadata to validate the PR exists."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    return _github_api_request(url)


def _fetch_pr_changed_files(
    owner: str,
    repo: str,
    pr_number: int,
    max_files: int = MAX_PR_FILES,
) -> list[PRChangedFile]:
    """Fetch files changed in a pull request, handling pagination.

    The GitHub API returns up to 30 files per page by default.
    We request up to 100 per page and paginate until all files are
    collected (bounded by max_files).
    """
    files: list[PRChangedFile] = []
    page = 1
    per_page = 100  # GitHub maximum per page

    while len(files) < max_files:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}"
            f"/pulls/{pr_number}/files?per_page={per_page}&page={page}"
        )
        page_data = _github_api_request(url)

        if not page_data:
            break

        for item in page_data:
            if len(files) >= max_files:
                break
            files.append(
                PRChangedFile(
                    filename=item.get("filename", ""),
                    status=item.get("status", "unknown"),
                    patch=item.get("patch"),
                    contents_url=item.get("contents_url"),
                    previous_filename=item.get("previous_filename"),
                )
            )

        if len(page_data) < per_page:
            break
        page += 1

    return files


def _fetch_file_content_from_url(contents_url: str) -> str:
    """Fetch and decode file content from a GitHub Contents API URL.

    The contents_url from the PR files endpoint includes a ?ref= parameter
    pointing to the PR head SHA, so the content is the PR version.
    """
    payload = _github_api_request(contents_url)
    raw_content = payload.get("content", "")
    try:
        return base64.b64decode(raw_content).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _is_analyzable_extension(file_path: str) -> bool:
    """Check if the file has an extension supported by CodeLens analyzers."""
    ext = get_file_extension(file_path)
    return ext in ANALYZABLE_EXTENSIONS


def review_pull_request(
    owner: str,
    repo: str,
    pr_number: int,
    repository_id: int,
) -> PRReviewResult:
    """Review a GitHub Pull Request by analyzing its changed files.

    Steps:
      1. Validate the PR exists by fetching its metadata.
      2. Fetch the list of changed files (with pagination).
      3. For each changed file:
         - Skip if status is 'removed' (file no longer exists).
         - Skip if extension is not analyzable (.py, .js, .ts, etc.).
         - Skip if file size exceeds MAX_FILE_SIZE_BYTES.
         - Fetch PR-version file content via contents_url.
         - Run the appropriate CodeLens analyzer.
      4. Return all findings in memory (no database writes).
    """
    # 1. Validate PR exists
    _fetch_pr_metadata(owner, repo, pr_number)

    # 2. Fetch changed files
    changed_files = _fetch_pr_changed_files(owner, repo, pr_number)

    result = PRReviewResult(
        repository_id=repository_id,
        pull_request_number=pr_number,
        files_changed=len(changed_files),
        files_analyzed=0,
    )

    # 3. Analyze each eligible file
    for cf in changed_files:
        # Skip removed files — content no longer exists
        if cf.status == "removed":
            result.skipped_files.append(f"{cf.filename} (removed)")
            continue

        # Skip files with unsupported extensions
        if not _is_analyzable_extension(cf.filename):
            result.skipped_files.append(f"{cf.filename} (unsupported extension)")
            continue

        # Skip files without a contents URL
        if not cf.contents_url:
            result.skipped_files.append(f"{cf.filename} (no content URL)")
            continue

        # Fetch file content (PR version)
        try:
            content = _fetch_file_content_from_url(cf.contents_url)
        except GitHubServiceError as exc:
            result.errors.append(f"{cf.filename}: {exc}")
            continue

        # Skip oversized files
        if len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            result.skipped_files.append(f"{cf.filename} (exceeds size limit)")
            continue

        # Run the existing analyzer dispatcher
        raw_findings = analyze_source_file(content=content, file_path=cf.filename)

        for rf in raw_findings:
            result.findings.append(
                PRFinding(
                    file_path=rf["file_path"],
                    line_number=rf.get("line_number"),
                    severity=rf["severity"],
                    category=rf["category"],
                    message=rf["message"],
                    rule_id=rf["rule_id"],
                )
            )

        result.files_analyzed += 1

    return result
