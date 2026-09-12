from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal, get_db
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.schemas.finding import (
    AIPRReviewResponse,
    AnalysisSummaryResponse,
    ApplyFixBranchRequest,
    ApplyFixBranchResponse,
    CreatePRFromBranchRequest,
    CreatePRFromBranchResponse,
    FindingExplanationResponse,
    FindingFixResponse,
    FindingResponse,
    FindingTestResponse,
    PRCommentRequest,
    PRCommentResponse,
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
    ScanStatusResponse,
    SourceFileResponse,
)
from app.services.analyzer import analyze_repository
from app.services.scan_progress import (
    get_scan_progress,
    is_scan_active,
    set_scan_progress,
    update_scan_progress,
)
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
from app.services.pr_commenter import (
    GitHubCredentialsUnavailableError,
    PRCommenterError,
    create_pr_review_comment,
)
from app.services.branch_fixer import (
    BranchCommitFailedError,
    BranchFixerError,
    BranchNotFoundError,
    GitHubCredentialsUnavailableError as BranchFixerCredentialsUnavailableError,
    PullRequestAlreadyExistsError,
    UnchangedFixError,
    apply_ai_fix_to_github_branch,
    create_pr_from_fix_branch,
)
from app.core.ai_errors import (
    AIQuotaExceededError,
    is_ai_quota_error,
    sanitize_ai_error,
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


def run_background_scan(repository_id: int) -> None:
    """Execute full scan (ingestion + static analysis) in the background with a fresh DB session."""
    db = SessionLocal()
    try:
        repository = db.get(Repository, repository_id)
        if not repository:
            set_scan_progress(
                repository_id=repository_id,
                status="failed",
                stage="failed",
                progress=0,
                message=f"Repository {repository_id} not found",
            )
            return

        set_scan_progress(
            repository_id=repository_id,
            status="running",
            stage="preparing",
            progress=5,
            files_processed=0,
            files_total=0,
            message="Preparing repository scan...",
        )

        owner = repository.owner
        repo_name = repository.name
        branch = repository.default_branch

        # Stage 1: Fetching files from GitHub
        set_scan_progress(
            repository_id=repository_id,
            status="running",
            stage="fetching_files",
            progress=10,
            files_processed=0,
            files_total=0,
            message="Fetching file tree from GitHub...",
        )

        def ingest_cb(processed: int, total: int, msg: str) -> None:
            pct = 10 + int((processed / max(total, 1)) * 40) if total > 0 else 10
            set_scan_progress(
                repository_id=repository_id,
                status="running",
                stage="fetching_files",
                progress=pct,
                files_processed=processed,
                files_total=total,
                message=msg,
            )

        ingest_res = ingest_repository(
            owner=owner,
            repo=repo_name,
            branch=branch,
            db=db,
            repository_id=repository_id,
            progress_callback=ingest_cb,
        )

        # Stage 2: Static Analysis
        set_scan_progress(
            repository_id=repository_id,
            status="running",
            stage="static_analysis",
            progress=50,
            files_processed=0,
            files_total=0,
            message="Starting static code analysis...",
        )

        def analyze_cb(processed: int, total: int, msg: str) -> None:
            pct = 50 + int((processed / max(total, 1)) * 45) if total > 0 else 50
            set_scan_progress(
                repository_id=repository_id,
                status="running",
                stage="static_analysis",
                progress=pct,
                files_processed=processed,
                files_total=total,
                message=msg,
            )

        analysis_res = analyze_repository(
            repository_id=repository_id,
            db=db,
            progress_callback=analyze_cb,
        )

        # Stage 3: Completed
        set_scan_progress(
            repository_id=repository_id,
            status="completed",
            stage="completed",
            progress=100,
            files_processed=analysis_res.files_analyzed,
            files_total=analysis_res.files_analyzed,
            message=f"Scan completed: {analysis_res.files_analyzed} files analyzed, {analysis_res.total_findings} findings found",
        )

    except Exception as exc:
        clean_error = sanitize_ai_error(exc) if hasattr(exc, "__str__") else "Scan failed unexpectedly"
        set_scan_progress(
            repository_id=repository_id,
            status="failed",
            stage="failed",
            progress=0,
            files_processed=0,
            files_total=0,
            message=clean_error,
        )
    finally:
        db.close()


@router.get("/{repository_id}/scan-status", response_model=ScanStatusResponse)
def get_repository_scan_status(
    repository_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve current scan progress for a repository."""
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )
    return get_scan_progress(repository_id)


@router.post("/{repository_id}/scan", response_model=ScanStatusResponse, status_code=status.HTTP_202_ACCEPTED)
def start_repository_scan(
    repository_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start a full repository scan (ingestion + static analysis) in the background.

    Immediately returns initial scan progress (queued/running).
    If a scan is already active for this repository, returns the existing active scan status
    without starting a duplicate task.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    if is_scan_active(repository_id):
        # Scan is already running: return existing status without starting duplicate
        return get_scan_progress(repository_id)

    # Initialize queued state
    initial_state = set_scan_progress(
        repository_id=repository_id,
        status="queued",
        stage="preparing",
        progress=0,
        files_processed=0,
        files_total=0,
        message="Scan queued...",
    )

    background_tasks.add_task(run_background_scan, repository_id)
    return initial_state


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

    set_scan_progress(
        repository_id=repository_id,
        status="running",
        stage="fetching_files",
        progress=10,
        files_processed=0,
        files_total=0,
        message="Fetching files from GitHub...",
    )

    def cb(processed: int, total: int, msg: str) -> None:
        pct = 10 + int((processed / max(total, 1)) * 85) if total > 0 else 10
        set_scan_progress(
            repository_id=repository_id,
            status="running",
            stage="fetching_files",
            progress=pct,
            files_processed=processed,
            files_total=total,
            message=msg,
        )

    try:
        result = ingest_repository(
            owner=repository.owner,
            repo=repository.name,
            branch=repository.default_branch,
            db=db,
            repository_id=repository.id,
        )
        set_scan_progress(
            repository_id=repository_id,
            status="completed",
            stage="completed",
            progress=100,
            files_processed=result.files_fetched,
            files_total=result.files_fetched,
            message=f"Ingested {result.files_stored} files",
        )
    except GitHubRepoNotFoundError as e:
        set_scan_progress(
            repository_id=repository_id,
            status="failed",
            stage="failed",
            progress=0,
            message=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except GitHubRateLimitError as e:
        set_scan_progress(
            repository_id=repository_id,
            status="failed",
            stage="failed",
            progress=0,
            message=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubServiceError as e:
        set_scan_progress(
            repository_id=repository_id,
            status="failed",
            stage="failed",
            progress=0,
            message=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )
    except Exception as e:
        set_scan_progress(
            repository_id=repository_id,
            status="failed",
            stage="failed",
            progress=0,
            message=sanitize_ai_error(e),
        )
        raise

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

    set_scan_progress(
        repository_id=repository_id,
        status="running",
        stage="static_analysis",
        progress=10,
        files_processed=0,
        files_total=0,
        message="Starting static code analysis...",
    )

    def cb(processed: int, total: int, msg: str) -> None:
        pct = 10 + int((processed / max(total, 1)) * 85) if total > 0 else 10
        set_scan_progress(
            repository_id=repository_id,
            status="running",
            stage="static_analysis",
            progress=pct,
            files_processed=processed,
            files_total=total,
            message=msg,
        )

    try:
        result = analyze_repository(
            repository_id=repository.id,
            db=db,
            progress_callback=cb,
        )
        set_scan_progress(
            repository_id=repository_id,
            status="completed",
            stage="completed",
            progress=100,
            files_processed=result.files_analyzed,
            files_total=result.files_analyzed,
            message=f"Analysis complete: {result.total_findings} findings detected",
        )
        return result
    except Exception as e:
        set_scan_progress(
            repository_id=repository_id,
            status="failed",
            stage="failed",
            progress=0,
            message=sanitize_ai_error(e),
        )
        raise


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
    except AIQuotaExceededError as e:
        raise e
    except ExplainerError as e:
        if is_ai_quota_error(e):
            raise AIQuotaExceededError() from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=sanitize_ai_error(e),
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
    except AIQuotaExceededError as e:
        raise e
    except FixerError as e:
        if is_ai_quota_error(e):
            raise AIQuotaExceededError() from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=sanitize_ai_error(e),
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
    except AIQuotaExceededError as e:
        raise e
    except TestGeneratorError as e:
        if is_ai_quota_error(e):
            raise AIQuotaExceededError() from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=sanitize_ai_error(e),
        )


@router.post(
    "/{repository_id}/findings/{finding_id}/apply-fix",
    response_model=ApplyFixBranchResponse,
)
def apply_fix_to_branch_endpoint(
    repository_id: int,
    finding_id: int,
    payload: ApplyFixBranchRequest | None = None,
    db: Session = Depends(get_db),
):
    """Apply an AI-generated fix for a finding to a newly created GitHub branch.

    1. Validates that the repository exists.
    2. Validates that the finding exists and belongs to that repository.
    3. Loads the relevant source file.
    4. Generates the AI fix using the existing fixer service.
    5. Applies the fix in memory using existing apply_fix_to_content logic.
    6. Rejects unchanged content with a clear error without creating a branch.
    7. Fetches the repository's current default branch and HEAD SHA.
    8. Creates a unique branch: codelens/fix/finding-{finding_id}-{short_id}.
    9. Commits the modified file to the new branch.
    10. Leaves the default branch untouched.
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

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini API is not configured (missing GEMINI_API_KEY)",
        )

    if not settings.github_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub credentials are unavailable (missing GITHUB_TOKEN)",
        )

    commit_msg = payload.commit_message if payload else None
    b_name = payload.branch_name if payload else None

    try:
        return apply_ai_fix_to_github_branch(
            repository=repository,
            finding=finding,
            source_file=source_file,
            commit_message=commit_msg,
            branch_name=b_name,
        )
    except UnchangedFixError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except BranchFixerCredentialsUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubRepoNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except BranchCommitFailedError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )
    except GitHubAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )
    except AIQuotaExceededError as e:
        raise e
    except FixerError as e:
        if is_ai_quota_error(e):
            raise AIQuotaExceededError() from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=sanitize_ai_error(e),
        )
    except BranchFixerError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )


@router.post(
    "/{repository_id}/findings/{finding_id}/create-pr",
    response_model=CreatePRFromBranchResponse,
)
def create_pr_from_branch_endpoint(
    repository_id: int,
    finding_id: int,
    payload: CreatePRFromBranchRequest,
    db: Session = Depends(get_db),
):
    """Create a GitHub Pull Request from an already-created AI fix branch.

    1. Validates repository exists in database.
    2. Validates finding exists and belongs to repository.
    3. Validates branch_name is provided.
    4. Verifies branch exists on GitHub.
    5. Determines repository default branch.
    6. Creates Pull Request with head=branch_name and base=default_branch.
    7. Returns Pull Request metadata.
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

    branch_name = payload.branch_name.strip()
    if not branch_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="branch_name cannot be empty",
        )

    if not settings.github_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub credentials are unavailable (missing GITHUB_TOKEN)",
        )

    try:
        return create_pr_from_fix_branch(
            repository=repository,
            finding=finding,
            branch_name=branch_name,
            title=payload.title,
            body=payload.body,
        )
    except BranchNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except PullRequestAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except BranchFixerCredentialsUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except GitHubRepoNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except GitHubAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )
    except BranchFixerError as e:
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
    except AIQuotaExceededError as e:
        raise e
    except AIPRReviewerError as e:
        if is_ai_quota_error(e):
            raise AIQuotaExceededError() from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=sanitize_ai_error(e),
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
    except AIQuotaExceededError as e:
        raise e
    except AIPRFixerError as e:
        if is_ai_quota_error(e):
            raise AIQuotaExceededError() from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=sanitize_ai_error(e),
        )


@router.post(
    "/{repository_id}/pull-requests/{pull_request_number}/comment",
    response_model=PRCommentResponse,
)
def comment_pull_request_endpoint(
    repository_id: int,
    pull_request_number: int,
    payload: PRCommentRequest = PRCommentRequest(),
    db: Session = Depends(get_db),
):
    """Post an AI-powered code review comment to a GitHub Pull Request.

    Runs diff-aware PR analysis, builds a formatted Markdown review comment,
    and posts it to the GitHub PR timeline using authenticated credentials.

    Does not modify repository contents, commits, branches, or database records.
    """
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id {repository_id} not found",
        )

    try:
        return create_pr_review_comment(
            repository=repository,
            pull_request_number=pull_request_number,
            summary=payload.summary,
            include_findings=payload.include_findings,
        )
    except GitHubCredentialsUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except (PRNotFoundError, GitHubRepoNotFoundError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except GitHubRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except (GitHubServiceError, PRCommenterError) as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )



