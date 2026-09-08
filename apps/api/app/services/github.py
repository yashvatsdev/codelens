import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class GitHubTreeEntry:
    """A single entry from a GitHub repository tree."""
    path: str
    type: str       # "blob" or "tree"
    sha: str
    size: int = 0   # size in bytes (only for blobs)


@dataclass(frozen=True)
class GitHubFileContent:
    """Contents of a single file fetched from GitHub."""
    path: str
    sha: str
    content: str
    size: int


# File extensions considered as analyzable source code.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".kt", ".kts",
    ".go", ".rs", ".rb",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".cs", ".swift", ".m",
    ".php", ".scala", ".ex", ".exs",
    ".sh", ".bash", ".zsh",
    ".sql",
    ".html", ".css", ".scss", ".less",
    ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".rst",
    ".xml", ".graphql", ".proto",
    ".dockerfile",
    ".tf", ".hcl",
})

# Maximum individual file size to fetch (100 KB).
MAX_FILE_SIZE_BYTES: int = 100_000


def is_supported_file(path: str, size: int = 0) -> bool:
    """Return True if the file path has a supported extension and is within size limits."""
    if size > MAX_FILE_SIZE_BYTES:
        return False
    # Handle extensionless files with known names
    basename = path.rsplit("/", 1)[-1].lower()
    if basename in ("dockerfile", "makefile", "rakefile", "gemfile", "procfile"):
        return True
    _, _, ext = basename.rpartition(".")
    if not ext:
        return False
    return f".{ext}" in SUPPORTED_EXTENSIONS


def _github_api_request(url: str, timeout: int = 10) -> dict | list:
    """Make a GET request to the GitHub API and return parsed JSON."""
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
            raise GitHubRepoNotFoundError(
                f"GitHub resource not found: {url}"
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
    """Fetch public repository metadata from GitHub API for a given repository URL."""
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
    payload = _github_api_request(api_url, timeout=timeout)
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


def fetch_repo_tree(
    owner: str,
    repo: str,
    branch: str = "main",
    timeout: int = 15,
) -> list[GitHubTreeEntry]:
    """Fetch the full recursive file tree of a GitHub repository.

    Uses the GitHub Git Trees API with ?recursive=1 to get the entire tree
    in a single request.

    Returns a list of GitHubTreeEntry objects.
    Raises GitHubServiceError subclasses on failure.
    """
    api_url = (
        f"https://api.github.com/repos/{owner}/{repo}"
        f"/git/trees/{branch}?recursive=1"
    )
    payload = _github_api_request(api_url, timeout=timeout)

    entries: list[GitHubTreeEntry] = []
    for item in payload.get("tree", []):
        entries.append(
            GitHubTreeEntry(
                path=item["path"],
                type=item["type"],
                sha=item["sha"],
                size=item.get("size", 0),
            )
        )
    return entries


def fetch_file_content(
    owner: str,
    repo: str,
    path: str,
    timeout: int = 10,
) -> GitHubFileContent:
    """Fetch the decoded text content of a single file from a GitHub repository.

    Uses the GitHub Contents API which returns base64-encoded content
    for files under 1 MB.

    Returns a GitHubFileContent with the decoded text.
    Raises GitHubServiceError subclasses on failure.
    """
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    payload = _github_api_request(api_url, timeout=timeout)

    import base64
    raw_content = payload.get("content", "")
    try:
        decoded = base64.b64decode(raw_content).decode("utf-8", errors="replace")
    except Exception:
        decoded = ""

    return GitHubFileContent(
        path=payload.get("path", path),
        sha=payload.get("sha", ""),
        content=decoded,
        size=payload.get("size", len(decoded)),
    )
