from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.schemas.finding import (
    AIPRReviewResponse,
    AnalysisSummaryResponse,
    FindingExplanationResponse,
    FindingFixResponse,
    FindingResponse,
    FindingTestResponse,
    PRFindingFixRequest,
    PRFindingFixResponse,
    PRReviewResponse,
)
from app.schemas.repository import (
    GitHubMetadataRequest,
    GitHubMetadataResponse,
    GitHubRepositoryCreate,
    IngestionResponse,
    RepositoryCreate,
    RepositoryResponse,
    SourceFileResponse,
)
from app.services.analyzer import analyze_repository
from app.services.github import (
    GitHubAPIError,
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
    GitHubServiceError,
    fetch_github_metadata,
    parse_github_url,
)
from app.services.explainer import (
    ExplainerError,
    FindingNotFoundError,
    GeminiNotConfiguredError,
    SourceFileNotFoundError,
    explain_finding,
)
from app.services.fixer import (
    FixerError,
    generate_fix,
)
from app.services.test_generator import (
    TestGeneratorError,
    generate_test,
)
from app.services.ingestion import ingest_repository
from app.services.pr_reviewer import (
    PRNotFoundError,
    review_pull_request,
)
from app.services.ai_pr_reviewer import (
    AIPRReviewerError,
    GeminiNotConfiguredError as AIPRGeminiNotConfiguredError,
    generate_ai_pr_review,
)
from app.services.ai_pr_fixer import (
    AIPRFixerError,
    GeminiNotConfiguredError as AIPRFixerGeminiNotConfiguredError,
    generate_pr_finding_fix,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.post("/github/metadata", response_model=GitHubMetadataResponse)
def get_github_repository_metadata(payload: GitHubMetadataRequest):
    """Fetch public GitHub repository metadata from a GitHub repository URL."""
    try:
        metadata = fetch_github_metadata(payload.url)
        return metadata
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except GitHubRepoNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.get("/github/metadata", response_model=GitHubMetadataResponse)
def get_github_repository_metadata_query(
    url: str = Query(..., description="GitHub repository URL (e.g. https://github.com/owner/repo)")
):
    """Fetch public GitHub repository metadata using query parameter."""
    try:
        metadata = fetch_github_metadata(url)
        return metadata
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except GitHubRepoNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post("/github", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED)
def connect_github_repository(
    payload: GitHubRepositoryCreate,
    db: Session = Depends(get_db),
):
    """Accept and validate a GitHub repository URL, store metadata, and return the repository."""
    try:
        parsed = parse_github_url(payload.url)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Check for existing repository by github_id or full_name
    existing = db.execute(
        select(Repository).where(
            or_(
                Repository.github_id == parsed.full_name,
                Repository.full_name == parsed.full_name,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository '{parsed.full_name}' is already connected",
        )

    default_branch = payload.default_branch or "main"

    repository = Repository(
        github_id=parsed.full_name,
        name=parsed.name,
        full_name=parsed.full_name,
        owner=parsed.owner,
        url=parsed.url,
        default_branch=default_branch,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED)
def create_repository(
    repository_in: RepositoryCreate,
    db: Session = Depends(get_db),
):
    # If explicit details are missing, attempt to parse them from the provided URL
    if not (repository_in.name and repository_in.owner and repository_in.full_name):
        try:
            parsed = parse_github_url(repository_in.url)
            name = repository_in.name or parsed.name
            owner = repository_in.owner or parsed.owner
            full_name = repository_in.full_name or parsed.full_name
            canonical_url = parsed.url
            github_id = repository_in.github_id or full_name
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
    else:
        name = repository_in.name
        owner = repository_in.owner
        full_name = repository_in.full_name
        canonical_url = repository_in.url
        github_id = repository_in.github_id or full_name

    existing = db.execute(
        select(Repository).where(
            or_(
                Repository.github_id == github_id,
                Repository.full_name == full_name,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository with this github_id or full_name already exists",
        )

    repository = Repository(
        github_id=github_id,
        name=name,
        full_name=full_name,
        owner=owner,
        url=canonical_url,
        default_branch=repository_in.default_branch or "main",
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


@router.get("", response_model=list[RepositoryResponse])
def get_repositories(db: Session = Depends(get_db)):
    repositories = db.execute(select(Repository).order_by(Repository.id)).scalars().all()
    return repositories


@router.get("/{repository_id}", response_model=RepositoryResponse)
def get_repository(
    repository_id: int,
    db: Session = Depends(get_db),
):
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )
    return repository


@router.post("/{repository_id}/ingest", response_model=IngestionResponse)
def ingest_repository_endpoint(
    repository_id: int,
    db: Session = Depends(get_db),
):
    """Ingest source files from a stored GitHub repository.

    Looks up the repository by ID, fetches the file tree from GitHub,
    retrieves supported source file contents, and returns an ingestion summary.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    try:
        result = ingest_repository(
            owner=repository.owner,
            repo=repository.name,
            branch=repository.default_branch,
            db=db,
            repository_id=repository.id,
        )
    except GitHubRepoNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )

    return result


@router.get("/{repository_id}/files", response_model=list[SourceFileResponse])
def get_repository_files(
    repository_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve stored source files for a repository (metadata only, no content)."""
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    files = db.execute(
        select(SourceFile)
        .where(SourceFile.repository_id == repository_id)
        .order_by(SourceFile.path)
    ).scalars().all()
    return files


@router.post("/{repository_id}/analyze", response_model=AnalysisSummaryResponse)
def analyze_repository_endpoint(
    repository_id: int,
    db: Session = Depends(get_db),
):
    """Run static code analysis on stored Python files for a repository."""
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    result = analyze_repository(repository_id=repository.id, db=db)
    return result


@router.get("/{repository_id}/findings", response_model=list[FindingResponse])
def get_repository_findings(
    repository_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve stored static analysis findings for a repository."""
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    findings = db.execute(
        select(Finding)
        .where(Finding.repository_id == repository_id)
        .order_by(Finding.id)
    ).scalars().all()
    return findings


@router.delete("/{repository_id}")
def delete_repository(
    repository_id: int,
    db: Session = Depends(get_db),
):
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )
    db.delete(repository)
    db.commit()
    return {
        "status": "ok",
        "message": f"Repository {repository_id} deleted successfully",
    }


@router.post(
    "/{repository_id}/findings/{finding_id}/explain",
    response_model=FindingExplanationResponse,
)
def explain_repository_finding(
    repository_id: int,
    finding_id: int,
    db: Session = Depends(get_db),
):
    """Generate an AI-powered explanation and remediation for a finding using Google Gemini."""
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    finding = db.get(Finding, finding_id)
    if not finding or finding.repository_id != repository_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding with id {finding_id} not found for repository {repository_id}",
        )

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini API is not configured (missing GEMINI_API_KEY)",
        )

    try:
        return explain_finding(
            repository=repository,
            finding=finding,
            db=db,
        )
    except FindingNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except SourceFileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except GeminiNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except ExplainerError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post(
    "/{repository_id}/findings/{finding_id}/fix",
    response_model=FindingFixResponse,
)
def fix_repository_finding_endpoint(
    repository_id: int,
    finding_id: int,
    db: Session = Depends(get_db),
):
    """Generate an AI-powered code fix for a finding using Google Gemini.

    This endpoint does not modify source files or repositories in GitHub or the database.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    finding = db.get(Finding, finding_id)
    if not finding or finding.repository_id != repository_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding with id {finding_id} not found for repository {repository_id}",
        )

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini API is not configured (missing GEMINI_API_KEY)",
        )

    if not finding.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding {finding_id} does not specify a file path",
        )

    source_file = db.execute(
        select(SourceFile).where(
            SourceFile.repository_id == repository.id,
            SourceFile.path == finding.file_path,
        )
    ).scalar_one_or_none()

    if not source_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file '{finding.file_path}' not found for repository {repository_id}",
        )

    try:
        return generate_fix(
            finding=finding,
            source_content=source_file.content,
        )
    except FixerError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post(
    "/{repository_id}/findings/{finding_id}/test",
    response_model=FindingTestResponse,
)
def generate_test_for_finding_endpoint(
    repository_id: int,
    finding_id: int,
    db: Session = Depends(get_db),
):
    """Generate an AI-powered unit test for a finding using Google Gemini.

    This endpoint does not modify source files or repositories in GitHub or the database.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    finding = db.get(Finding, finding_id)
    if not finding or finding.repository_id != repository_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding with id {finding_id} not found for repository {repository_id}",
        )

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini API is not configured (missing GEMINI_API_KEY)",
        )

    if not finding.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Finding {finding_id} does not specify a file path",
        )

    source_file = db.execute(
        select(SourceFile).where(
            SourceFile.repository_id == repository.id,
            SourceFile.path == finding.file_path,
        )
    ).scalar_one_or_none()

    if not source_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source file '{finding.file_path}' not found for repository {repository_id}",
        )

    try:
        return generate_test(
            finding=finding,
            source_content=source_file.content,
        )
    except TestGeneratorError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post(
    "/{repository_id}/pull-requests/{pull_request_number}/review",
    response_model=PRReviewResponse,
)
def review_pull_request_endpoint(
    repository_id: int,
    pull_request_number: int,
    db: Session = Depends(get_db),
):
    """Review a GitHub Pull Request by analyzing its changed files.

    Fetches the files changed in the specified PR, runs CodeLens static
    analyzers on supported file types, and returns findings in memory.

    Nothing is written to the database.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    try:
        result = review_pull_request(
            owner=repository.owner,
            repo=repository.name,
            pr_number=pull_request_number,
            repository_id=repository.id,
        )
    except PRNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pull request #{pull_request_number} not found: {e}",
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )

    return result


@router.post(
    "/{repository_id}/pull-requests/{pull_request_number}/ai-review",
    response_model=AIPRReviewResponse,
)
def ai_review_pull_request_endpoint(
    repository_id: int,
    pull_request_number: int,
    db: Session = Depends(get_db),
):
    """Perform an AI-powered code review of a GitHub Pull Request using Google Gemini.

    Reuses Phase 1 PR review to fetch changed files and run static analysis,
    then generates an intelligent review with risk assessment, key findings, and recommendations.

    Nothing is written to the database.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini API is not configured (missing GEMINI_API_KEY)",
        )

    try:
        review_result = review_pull_request(
            owner=repository.owner,
            repo=repository.name,
            pr_number=pull_request_number,
            repository_id=repository.id,
        )
    except PRNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pull request #{pull_request_number} not found: {e}",
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )

    try:
        return generate_ai_pr_review(
            review_result=review_result,
            repo_full_name=repository.full_name,
        )
    except AIPRGeminiNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except AIPRReviewerError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post(
    "/{repository_id}/pull-requests/{pull_request_number}/findings/fix",
    response_model=PRFindingFixResponse,
)
def fix_pr_finding_endpoint(
    repository_id: int,
    pull_request_number: int,
    payload: PRFindingFixRequest,
    db: Session = Depends(get_db),
):
    """Generate an AI-powered code fix for a PR review finding using Google Gemini.

    Reuses Phase 1 PR review to obtain changed file contents in memory,
    validates the file and line number, and returns the proposed fix, diff, and
    resulting code preview.

    Nothing is written to the database or GitHub.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini API is not configured (missing GEMINI_API_KEY)",
        )

    try:
        review_result = review_pull_request(
            owner=repository.owner,
            repo=repository.name,
            pr_number=pull_request_number,
            repository_id=repository.id,
        )
    except PRNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pull request #{pull_request_number} not found: {e}",
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )

    if payload.file_path not in review_result.file_contents:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{payload.file_path}' not found in PR #{pull_request_number} changed files",
        )

    file_content = review_result.file_contents[payload.file_path]
    lines = file_content.splitlines()
    total_lines = len(lines)
    if total_lines == 0 or payload.line_number < 1 or payload.line_number > total_lines:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Line number {payload.line_number} is invalid for file '{payload.file_path}' (total lines: {total_lines})",
        )

    try:
        return generate_pr_finding_fix(
            file_contents=review_result.file_contents,
            file_path=payload.file_path,
            line_number=payload.line_number,
            issue=payload.issue,
            severity=payload.severity,
            category=payload.category,
            message=payload.message,
        )
    except AIPRFixerGeminiNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except AIPRFixerError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


