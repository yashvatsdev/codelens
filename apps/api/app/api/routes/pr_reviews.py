from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.pr_review import PRReview
from app.models.repository import Repository
from app.models.user import User
from app.schemas.pr_review import PRReviewDetailResponse, PRReviewHistoryItem

router = APIRouter(prefix="/pr-reviews", tags=["pr-reviews"])


@router.get("", response_model=list[PRReviewHistoryItem])
def get_pr_review_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = 50,
):
    """Get the current user's PR review history."""
    reviews = (
        db.query(PRReview, Repository.name, Repository.full_name)
        .join(Repository)
        .filter(PRReview.user_id == current_user.id)
        .order_by(PRReview.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for review, repo_name, repo_full_name in reviews:
        # Pydantic will extract attributes from review, but we need to supply findings_count
        item = PRReviewHistoryItem(
            id=review.id,
            repository_id=review.repository_id,
            pull_request_number=review.pull_request_number,
            summary=review.summary,
            risk_level=review.risk_level,
            findings_count=len(review.key_findings),
            created_at=review.created_at,
            repository_name=repo_name,
            repository_full_name=repo_full_name,
        )
        result.append(item)
    return result


@router.get("/{review_id}", response_model=PRReviewDetailResponse)
def get_pr_review_detail(
    review_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific PR review by ID."""
    review_data = (
        db.query(PRReview, Repository.name, Repository.full_name)
        .join(Repository)
        .filter(PRReview.id == review_id, PRReview.user_id == current_user.id)
        .first()
    )

    if not review_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PR Review {review_id} not found"
        )

    review, repo_name, repo_full_name = review_data
    return PRReviewDetailResponse(
        id=review.id,
        repository_id=review.repository_id,
        pull_request_number=review.pull_request_number,
        summary=review.summary,
        risk_level=review.risk_level,
        overall_assessment=review.overall_assessment,
        key_findings=review.key_findings,
        recommendations=review.recommendations,
        created_at=review.created_at,
        repository_name=repo_name,
        repository_full_name=repo_full_name,
    )

