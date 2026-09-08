from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.repository import Repository
from app.schemas.repository import RepositoryCreate, RepositoryResponse

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED)
def create_repository(
    repository_in: RepositoryCreate,
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(Repository).where(Repository.github_id == repository_in.github_id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository with this github_id already exists",
        )

    repository = Repository(
        github_id=repository_in.github_id,
        name=repository_in.name,
        full_name=repository_in.full_name,
        owner=repository_in.owner,
        url=repository_in.url,
        default_branch=repository_in.default_branch,
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

