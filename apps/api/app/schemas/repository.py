from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RepositoryBase(BaseModel):
    github_id: str
    name: str
    full_name: str
    owner: str
    url: str
    default_branch: str = "main"


class RepositoryCreate(BaseModel):
    """Schema for creating a repository manually or with automatic URL parsing."""
    url: str
    github_id: str | None = None
    name: str | None = None
    full_name: str | None = None
    owner: str | None = None
    default_branch: str = "main"


class GitHubRepositoryCreate(BaseModel):
    """Schema for importing a repository directly from a GitHub URL."""
    url: str = Field(..., description="GitHub repository URL (e.g. https://github.com/owner/repo)")
    default_branch: str | None = Field(default=None, description="Optional default branch override")


class GitHubMetadataRequest(BaseModel):
    """Schema for fetching GitHub repository metadata by URL."""
    url: str = Field(..., description="GitHub repository URL (e.g. https://github.com/owner/repo)")


class GitHubMetadataResponse(BaseModel):
    """Schema representing public metadata fetched for a GitHub repository."""
    owner: str
    name: str
    full_name: str
    description: str | None = None
    default_branch: str
    url: str

    model_config = ConfigDict(from_attributes=True)


class RepositoryResponse(RepositoryBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IngestionFileResponse(BaseModel):
    """Metadata about a single ingested file."""
    path: str
    sha: str
    size: int


class IngestionResponse(BaseModel):
    """Summary returned after ingesting a repository's source files."""
    owner: str
    repo: str
    branch: str
    total_tree_entries: int
    files_identified: int
    files_fetched: int
    files_skipped: int
    fetched_files: list[IngestionFileResponse] = []
    errors: list[str] = []
