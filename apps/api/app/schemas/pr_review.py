from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.finding import AIPRKeyFindingResponse

class PRReviewHistoryItem(BaseModel):
    id: int
    repository_id: int
    pull_request_number: int
    summary: str
    risk_level: str
    findings_count: int
    created_at: datetime
    
    repository_name: str | None = None
    repository_full_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PRReviewDetailResponse(BaseModel):
    id: int
    repository_id: int
    pull_request_number: int
    summary: str
    risk_level: str
    overall_assessment: str
    key_findings: list[AIPRKeyFindingResponse] = []
    recommendations: list[str] = []
    created_at: datetime
    
    repository_name: str | None = None
    repository_full_name: str | None = None

    model_config = ConfigDict(from_attributes=True)

