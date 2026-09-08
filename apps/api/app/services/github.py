import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


class GitHubServiceError(Exception):
    """Base exception for GitHub service errors."""
    pass


class GitHubRepoNotFoundError(GitHubServiceError):
    """Raised when a repository is not found or is private."""
    pass


class GitHubRateLimitError(GitHubServiceError):
    """Raised when the GitHub API rate limit is exceeded."""
    pass


class GitHubAPIError(GitHubServiceError):
    """Raised when GitHub API returns an unexpected error."""
    pass


@dataclass(frozen=True)
class ParsedGitHubRepo:
    owner: str
    name: str
    full_name: str
    url: str


@dataclass(frozen=True)
class GitHubRepoMetadata:
    owner: str
    name: str
    full_name: str
    description: str | None
    default_branch: str
    url: str


def parse_github_url(raw_url: str) -> ParsedGitHubRepo:
    """Parse and validate a GitHub repository URL.

    Accepts HTTPS, HTTP, domain-prefixed, or SSH clone URLs:
      - https://github.com/owner/repo
      - https://github.com/owner/repo.git
      - http://github.com/owner/repo/
      - git@github.com:owner/repo.git
      - github.com/owner/repo

    Returns ParsedGitHubRepo with normalized owner, name, full_name, and canonical URL.
    Raises ValueError with descriptive reason if invalid.
    """
    if not raw_url or not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError("Repository URL cannot be empty")

    trimmed = raw_url.strip()

    # 1. SSH format: git@github.com:owner/repo(.git)
    ssh_match = re.match(
        r"^git@github\.com:(?P<owner>[a-zA-Z0-9._-]+)/(?P<repo>[a-zA-Z0-9._-]+?)(?:\.git)?(?:/)?$",
        trimmed,
    )
    if ssh_match:
        owner = ssh_match.group("owner")
        repo = ssh_match.group("repo")
        full_name = f"{owner}/{repo}"
        return ParsedGitHubRepo(
            owner=owner,
            name=repo,
            full_name=full_name,
            url=f"https://github.com/{full_name}",
        )

    # 2. HTTP/HTTPS or scheme-less format
    url_to_parse = trimmed
    if not url_to_parse.startswith(("http://", "https://")):
        url_to_parse = f"https://{url_to_parse}"

    parsed = urlparse(url_to_parse)
    netloc = (parsed.netloc or "").lower()

    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]

    if netloc not in ("github.com", "www.github.com"):
        raise ValueError("Only GitHub repository URLs (github.com) are supported")

    path_segments = [s for s in parsed.path.strip("/").split("/") if s]
    if len(path_segments) != 2:
        raise ValueError(
            "Invalid GitHub repository URL: must contain both owner and repository name (e.g. 'https://github.com/owner/repo')"
        )

    owner, repo = path_segments
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not repo:
        raise ValueError("Invalid GitHub repository URL: repository name is missing")

    name_pattern = r"^[a-zA-Z0-9._-]+$"
    if not re.match(name_pattern, owner) or not re.match(name_pattern, repo):
        raise ValueError("Invalid GitHub repository URL: contains unsupported characters")

    full_name = f"{owner}/{repo}"
    return ParsedGitHubRepo(
        owner=owner,
        name=repo,
        full_name=full_name,
        url=f"https://github.com/{full_name}",
    )


def fetch_github_metadata(raw_url: str, timeout: int = 10) -> GitHubRepoMetadata:
    """Fetch public repository metadata from GitHub API for a given repository URL.

    Extracts:
      - owner (str)
      - repository name (str)
      - description (str | None)
      - default_branch (str)
      - url (str)

    Raises:
      - ValueError: If the provided URL is invalid.
      - GitHubRepoNotFoundError: If repository is not found or private (HTTP 404).
      - GitHubRateLimitError: If GitHub API rate limits are reached (HTTP 403).
      - GitHubAPIError: On any other GitHub or network connection error.
    """
    parsed = parse_github_url(raw_url)

    api_url = f"https://api.github.com/repos/{parsed.owner}/{parsed.name}"
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "CodeLens-App",
            "Accept": "application/vnd.github+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise GitHubRepoNotFoundError(
                f"GitHub repository '{parsed.full_name}' was not found or is private."
            ) from err
        if err.code == 403:
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

    owner = payload.get("owner", {}).get("login") or parsed.owner
    name = payload.get("name") or parsed.name
    full_name = payload.get("full_name") or f"{owner}/{name}"
    description = payload.get("description")
    default_branch = payload.get("default_branch") or "main"
    html_url = payload.get("html_url") or parsed.url

    return GitHubRepoMetadata(
        owner=owner,
        name=name,
        full_name=full_name,
        description=description,
        default_branch=default_branch,
        url=html_url,
    )
