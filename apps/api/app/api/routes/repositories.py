from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.repository import Repository
from app.schemas.repository import (
    GitHubRepositoryCreate,
    RepositoryCreate,
    RepositoryResponse,
)
from app.services.github import parse_github_url

router = APIRouter(prefix="/repositories", tags=["repositories"])


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
