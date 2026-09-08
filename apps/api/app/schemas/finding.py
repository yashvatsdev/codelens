from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FindingBase(BaseModel):
    file_path: str | None = None
    line_number: int | None = None
    severity: str = Field(..., description="Finding severity (e.g. info, warning, error, critical)")
    category: str = Field(..., description="Finding category (e.g. security, performance, style, bug)")
    message: str = Field(..., description="Description of the finding")
    rule_id: str = Field(..., description="Identifier for the rule triggered")


class FindingCreate(FindingBase):
    repository_id: int


class FindingResponse(FindingBase):
    id: int
    repository_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

