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
    code_context: str | None = Field(default=None, description="Source code snippet around the finding")
    start_line: int | None = Field(default=None, description="Starting line number of code context")
    end_line: int | None = Field(default=None, description="Ending line number of code context")



class AIPRReviewResponse(BaseModel):
    """Structured AI-powered PR review response."""
    summary: str = Field(..., description="Brief summary of the PR review")
    risk_level: str = Field(..., description="Overall risk level: low, medium, high, or critical")
    overall_assessment: str = Field(..., description="Detailed overall assessment")
    key_findings: list[AIPRKeyFindingResponse] = []
    recommendations: list[str] = []


class PRFindingFixRequest(BaseModel):
    """Request payload for generating an AI fix for a PR finding."""
    file_path: str = Field(..., description="Path to the file containing the finding")
    line_number: int = Field(..., description="Line number of the finding")
    issue: str = Field(..., description="Issue description")
    severity: str = Field(..., description="Severity level")
    category: str = Field(..., description="Category of the finding")
    message: str = Field(..., description="Detailed finding message")


class PRFindingFixResponse(BaseModel):
    """Response returned after generating an AI fix for a PR finding."""
    file_path: str
    line_number: int | None = None
    explanation: str
    original_code: str
    fixed_code: str
    diff: str | None = None
    resulting_code: str | None = None


class PRCommentRequest(BaseModel):
    """Request payload for posting a PR review comment to GitHub."""
    summary: str | None = Field(default=None, description="Optional custom review summary")
    include_findings: bool = Field(default=True, description="Whether to include detailed key findings in the comment")


class PRCommentResponse(BaseModel):
    """Response returned after posting a PR review comment to GitHub."""
    repository_id: int
    pull_request_number: int
    comment_id: int | str
    comment_url: str | None = None
    risk_level: str
    findings_posted: int


class ApplyFixBranchRequest(BaseModel):
    """Optional request payload for applying an AI fix to a GitHub branch."""
    commit_message: str | None = Field(default=None, description="Optional custom commit message")
    branch_name: str | None = Field(default=None, description="Optional custom branch name")


class ApplyFixBranchResponse(BaseModel):
    """Response returned after applying an AI fix to a new GitHub branch."""
    repository_id: int
    finding_id: int
    branch_name: str
    commit_sha: str
    commit_url: str | None = None
    file_path: str
    message: str = "AI fix applied successfully to GitHub branch."


class CreatePRFromBranchRequest(BaseModel):
    """Request payload for creating a GitHub Pull Request from a fix branch."""
    branch_name: str = Field(..., min_length=1, description="Name of the head branch containing the fix")
    title: str | None = Field(default=None, description="Optional custom Pull Request title")
    body: str | None = Field(default=None, description="Optional custom Pull Request description")


class CreatePRFromBranchResponse(BaseModel):
    """Response returned after successfully creating a GitHub Pull Request."""
    repository_id: int
    finding_id: int
    pull_request_number: int
    pull_request_url: str
    branch_name: str
    base_branch: str
    title: str
    message: str = "Pull Request created successfully."


