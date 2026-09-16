from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.repository import Repository
    from app.models.user import User


class PRReview(Base):
    __tablename__ = "pr_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(50), nullable=False)
    overall_assessment: Mapped[str] = mapped_column(Text, nullable=False)
    
    key_findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    recommendations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")
    repository: Mapped["Repository"] = relationship("Repository")

    def __repr__(self) -> str:
        return f"<PRReview id={self.id} repository_id={self.repository_id} pr_number={self.pull_request_number}>"

