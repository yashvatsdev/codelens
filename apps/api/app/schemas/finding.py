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


class AnalysisSummaryResponse(BaseModel):
    repository_id: int
    files_analyzed: int
    total_findings: int
    findings: list[FindingResponse] = []

    model_config = ConfigDict(from_attributes=True)


class FindingExplanationResponse(BaseModel):
    finding_id: int
    explanation: str
    remediation: str | None = None


class FindingFixResponse(BaseModel):
    finding_id: int
    explanation: str
    original_code: str
    fixed_code: str
    diff: str | None = None
    resulting_code: str | None = None


class FindingTestResponse(BaseModel):
    finding_id: int
    test_framework: str
    test_file: str
    test_code: str
    explanation: str


class PRFindingResponse(BaseModel):
    """A single static analysis finding from a PR review (no database ID)."""
    file_path: str
    line_number: int | None = None
    severity: str = Field(..., description="Finding severity (e.g. info, warning, error)")
    category: str = Field(..., description="Finding category (e.g. security, style, bug)")
    message: str = Field(..., description="Description of the finding")
    rule_id: str = Field(..., description="Identifier for the rule triggered")


class PRReviewResponse(BaseModel):
    """Summary returned after reviewing a GitHub Pull Request."""
    repository_id: int
    pull_request_number: int
    files_changed: int
    files_analyzed: int
    findings: list[PRFindingResponse] = []


class AIPRKeyFindingResponse(BaseModel):
    """A single AI-analyzed key finding from a PR review."""
    file_path: str
    line_number: int | None = None
    severity: str = Field(..., description="Finding severity (e.g. error, warning, info)")
    category: str = Field(..., description="Finding category (e.g. security, bug, style)")
    issue: str = Field(..., description="Clear description of what is wrong")
    impact: str = Field(..., description="Why this matters and potential consequences")
    recommendation: str = Field(..., description="Specific actionable fix recommendation")


class AIPRReviewResponse(BaseModel):
    """Structured AI-powered PR review response."""
    summary: str = Field(..., description="Brief summary of the PR review")
    risk_level: str = Field(..., description="Overall risk level: low, medium, high, or critical")
    overall_assessment: str = Field(..., description="Detailed overall assessment")
    key_findings: list[AIPRKeyFindingResponse] = []
    recommendations: list[str] = []
