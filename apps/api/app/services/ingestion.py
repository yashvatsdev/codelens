"""Repository ingestion service.

Orchestrates fetching the file tree and source file contents from a
GitHub repository, filtering for supported source files only,
and optionally persisting them to the database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.source_file import SourceFile
from app.services.github import (
    GitHubFileContent,
    GitHubServiceError,
    GitHubTreeEntry,
    MAX_FILE_SIZE_BYTES,
    fetch_file_content,
    fetch_repo_tree,
    is_supported_file,
)


class IngestionError(Exception):
    """Base exception for repository ingestion errors."""
    pass


class RepositorySourceTooLargeError(IngestionError):
    """Raised when the aggregate size of supported source files exceeds the limit."""
    pass


# Aggregate limit: Total size of all fetched source files cannot exceed 20 MB.
MAX_TOTAL_SOURCE_BYTES: int = 20_000_000


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
    files_stored: int = 0
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
    db: Session | None = None,
    repository_id: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> IngestionResult:
    """Ingest source files from a public GitHub repository.

    Steps:
      1. Fetch the full recursive tree for the given branch.
      2. Filter for supported source files (by extension and size limits).
      3. Apply aggregate size limits.
      4. Fetch the content of each supported file (up to max_files).
      5. If db and repository_id are provided, persist files to the database.
      6. Return an IngestionResult summary.

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
    total_to_fetch = len(entries_to_fetch)

    # Enforce aggregate size limit BEFORE fetching contents
    total_bytes_expected = sum(entry.size for entry in entries_to_fetch)
    if total_bytes_expected > MAX_TOTAL_SOURCE_BYTES:
        raise RepositorySourceTooLargeError(
            f"Repository source files total {total_bytes_expected} bytes, "
            f"exceeding the maximum allowed size of {MAX_TOTAL_SOURCE_BYTES} bytes."
        )

    if progress_callback is not None:
        progress_callback(0, total_to_fetch, f"Found {total_to_fetch} files to fetch")

    fetched_contents: list[GitHubFileContent] = []

    # Track actual bytes fetched just in case GitHub size metadata is inaccurate
    actual_bytes_fetched = 0

    for idx, entry in enumerate(entries_to_fetch, start=1):
        if actual_bytes_fetched + entry.size > MAX_TOTAL_SOURCE_BYTES:
             # Failsafe if size metadata was wrong or we somehow exceeded limit
             raise RepositorySourceTooLargeError(
                 f"Repository source exceeds the maximum allowed size of {MAX_TOTAL_SOURCE_BYTES} bytes."
             )

        try:
            file_content = fetch_file_content(owner, repo, entry.path)
            if len(file_content.content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
                raise RepositorySourceTooLargeError("Source file exceeds the maximum allowed size")

            # Additional safety check against actual content size
            if actual_bytes_fetched + len(file_content.content.encode('utf-8')) > MAX_TOTAL_SOURCE_BYTES:
                raise RepositorySourceTooLargeError(
                    f"Repository source exceeds the maximum allowed size of {MAX_TOTAL_SOURCE_BYTES} bytes."
                )

            fetched_contents.append(file_content)
            actual_bytes_fetched += len(file_content.content.encode('utf-8'))

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

        if progress_callback is not None:
            progress_callback(idx, total_to_fetch, f"Fetched {idx}/{total_to_fetch} files")

    # Persist to database if session is provided
    if db is not None and repository_id is not None and fetched_contents:
        # Delete existing source files for this repo (full re-ingest)
        db.execute(
            delete(SourceFile).where(SourceFile.repository_id == repository_id)
        )

        for fc in fetched_contents:
            source_file = SourceFile(
                repository_id=repository_id,
                path=fc.path,
                sha=fc.sha,
                content=fc.content,
                size=fc.size,
            )
            db.add(source_file)

        db.commit()
        result.files_stored = len(fetched_contents)

    return result
