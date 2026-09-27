"""Dashboard-level API routes.

Provides aggregated/cross-repository data for the CodeLens dashboard UI.
All endpoints are scoped to the authenticated user's own repositories.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.repository import Repository
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class FindingsTrendPoint(BaseModel):
    """A single point in the findings trend time series."""

    date: datetime
    findings: int
    errors: int
    warnings: int
    info: int


@router.get("/findings-trend", response_model=list[FindingsTrendPoint])
def get_findings_trend(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[FindingsTrendPoint]:
    """Return historical findings snapshot data for all repositories owned by
    the authenticated user.

    Each point represents one completed analysis run. Multiple repositories
    are aggregated per timestamp (if they happen to share the exact same
    scanned_at value) — in practice each snapshot is per-repository so the
    result is the union of all snapshots ordered by time.

    Ownership is enforced: only snapshots attached to repositories whose
    user_id matches current_user.id are returned.
    """
    # Fetch IDs of repositories owned by the current user
    repo_ids_stmt = select(Repository.id).where(
        Repository.user_id == current_user.id
    )
    repo_ids = db.execute(repo_ids_stmt).scalars().all()

    if not repo_ids:
        return []

    # Fetch all snapshots for those repositories, ordered by time
    snapshots_stmt = (
        select(AnalysisSnapshot)
        .where(AnalysisSnapshot.repository_id.in_(repo_ids))
        .order_by(AnalysisSnapshot.scanned_at.asc())
    )
    snapshots = db.execute(snapshots_stmt).scalars().all()

    return [
        FindingsTrendPoint(
            date=snap.scanned_at,
            findings=snap.total_findings,
            errors=snap.errors,
            warnings=snap.warnings,
            info=snap.info,
        )
        for snap in snapshots
    ]
