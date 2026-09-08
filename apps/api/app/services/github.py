import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ParsedGitHubRepo:
    owner: str
    name: str
    full_name: str
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

