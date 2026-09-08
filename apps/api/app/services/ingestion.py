"""Repository ingestion service.

Orchestrates fetching the file tree and source file contents from a
GitHub repository, filtering for supported source files only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.github import (
    GitHubFileContent,
    GitHubServiceError,
    GitHubTreeEntry,
    fetch_file_content,
    fetch_repo_tree,
    is_supported_file,
)


@dataclass
class IngestionResult:
    """Summary returned after ingesting a repository's source files."""
    owner: str
    repo: str
    branch: str
    total_tree_entries: int
    files_identified: int
    files_fetched: int
    files_skipped: int
    fetched_files: list[IngestionFileEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IngestionFileEntry:
    """Metadata about a single ingested file."""
    path: str
    sha: str
    size: int


def ingest_repository(
    owner: str,
    repo: str,
    branch: str = "main",
    max_files: int = 200,
) -> IngestionResult:
    """Ingest source files from a public GitHub repository.

    Steps:
      1. Fetch the full recursive tree for the given branch.
      2. Filter for supported source files (by extension and size).
      3. Fetch the content of each supported file (up to max_files).
      4. Return an IngestionResult summary.

    Files that fail to fetch are recorded in errors and skipped.
    """
    tree = fetch_repo_tree(owner, repo, branch)

    # Filter: only blobs that pass the supported-file check
    supported_entries: list[GitHubTreeEntry] = [
        entry for entry in tree
        if entry.type == "blob" and is_supported_file(entry.path, entry.size)
    ]

    result = IngestionResult(
        owner=owner,
        repo=repo,
        branch=branch,
        total_tree_entries=len(tree),
        files_identified=len(supported_entries),
        files_fetched=0,
        files_skipped=0,
    )

    # Limit the number of files we fetch in one ingestion run
    entries_to_fetch = supported_entries[:max_files]
    result.files_skipped = len(supported_entries) - len(entries_to_fetch)

    for entry in entries_to_fetch:
        try:
            file_content = fetch_file_content(owner, repo, entry.path)
            result.fetched_files.append(
                IngestionFileEntry(
                    path=file_content.path,
                    sha=file_content.sha,
                    size=file_content.size,
                )
            )
            result.files_fetched += 1
        except GitHubServiceError as exc:
            result.errors.append(f"{entry.path}: {exc}")

    return result
