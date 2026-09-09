from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.finding import Finding
    from app.models.source_file import SourceFile


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    github_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(100), default="main", server_default="main", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    findings: Mapped[list["Finding"]] = relationship(
        "Finding", back_populates="repository", cascade="all, delete-orphan"
    )
    source_files: Mapped[list["SourceFile"]] = relationship(
        "SourceFile", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Repository id={self.id} full_name={self.full_name!r}>"


