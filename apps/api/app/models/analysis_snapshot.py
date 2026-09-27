"""AnalysisSnapshot model.

Records a point-in-time summary of finding counts after each successful
repository analysis. These snapshots drive the Findings Trend chart on the
CodeLens dashboard.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.repository import Repository


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    total_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="analysis_snapshots"
    )

    __table_args__ = (
        Index("ix_analysis_snapshots_repository_id", "repository_id"),
        Index("ix_analysis_snapshots_scanned_at", "scanned_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AnalysisSnapshot id={self.id} repository_id={self.repository_id} "
            f"total={self.total_findings} scanned_at={self.scanned_at}>"
        )
