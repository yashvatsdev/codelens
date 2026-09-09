import json
import logging
from typing import Any
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.schemas.finding import FindingExplanationResponse

logger = logging.getLogger(__name__)


class ExplainerError(Exception):
    """Base exception for explainer service errors."""
    pass


class GeminiNotConfiguredError(ExplainerError):
    """Raised when GEMINI_API_KEY is not configured."""
    pass


class FindingNotFoundError(ExplainerError):
    """Raised when a finding is not found or does not belong to the repository."""
    pass


class SourceFileNotFoundError(ExplainerError):
    """Raised when the source file for a finding cannot be found."""
    pass


class ExplanationSchema(BaseModel):
    explanation: str
    remediation: str | None = None


def extract_code_context(content: str, line_number: int | None, window: int = 10) -> str:
    """Extract approximately `window` lines before and after `line_number` bounded by file lines."""
    lines = content.splitlines()
    if not lines:
        return "(empty file)"

    if line_number is None or line_number <= 0:
        start_idx = 0
        end_idx = min(len(lines), window * 2)
    else:
        target_idx = line_number - 1
        start_idx = max(0, target_idx - window)
        end_idx = min(len(lines), target_idx + window + 1)

    snippet_lines = []
    for idx in range(start_idx, end_idx):
        curr_line_num = idx + 1
        marker = "->" if curr_line_num == line_number else "  "
        snippet_lines.append(f"{marker} {curr_line_num:4d} | {lines[idx]}")

    return "\n".join(snippet_lines)


def build_explainer_prompt(
    rule_id: str,
    severity: str,
    category: str,
    message: str,
    file_path: str | None,
    line_number: int | None,
    code_context: str,
) -> str:
    """Build a concise, structured prompt for Gemini."""
    return f"""You are an expert static code analysis assistant. Analyze the following code finding and provide a clear explanation and remediation.

FINDING METADATA:
- Rule ID: {rule_id}
- Severity: {severity}
- Category: {category}
- Message: {message}
- File Path: {file_path or 'unknown'}
- Line Number: {line_number if line_number is not None else 'N/A'}

RELEVANT SOURCE CODE:
```
{code_context}
```

Please explain:
1. What the issue is
2. Why it matters (security, reliability, maintainability, or performance)
3. How to fix it (concrete remediation guidance or code example)

Return your answer in valid JSON matching this schema:
{{
  "explanation": "<What the issue is and why it matters>",
  "remediation": "<Concrete fix or remediation advice>"
}}
"""


def explain_finding(
    repository: Repository,
    finding: Finding,
    db: Session,
    gemini_client: Any = None,
) -> FindingExplanationResponse:
    """Explain a finding using Google Gemini.

    Raises:
        FindingNotFoundError: If finding does not belong to the repository.
        SourceFileNotFoundError: If the source file cannot be found.
        GeminiNotConfiguredError: If GEMINI_API_KEY is not configured.
        ExplainerError: For any other API / generation failure.
    """
    if finding.repository_id != repository.id:
        raise FindingNotFoundError(
            f"Finding {finding.id} does not belong to repository {repository.id}"
        )

    if not finding.file_path:
        raise SourceFileNotFoundError("Finding does not specify a file path")

    source_file = db.execute(
        select(SourceFile).where(
            SourceFile.repository_id == repository.id,
            SourceFile.path == finding.file_path,
        )
    ).scalar_one_or_none()

    if not source_file:
        raise SourceFileNotFoundError(
            f"Source file '{finding.file_path}' not found for repository {repository.id}"
        )

    if not settings.gemini_api_key and gemini_client is None:
        raise GeminiNotConfiguredError("GEMINI_API_KEY is not configured")

    code_context = extract_code_context(
        content=source_file.content,
        line_number=finding.line_number,
        window=10,
    )

    prompt = build_explainer_prompt(
        rule_id=finding.rule_id,
        severity=finding.severity,
        category=finding.category,
        message=finding.message,
        file_path=finding.file_path,
        line_number=finding.line_number,
        code_context=code_context,
    )

    client = gemini_client
    if client is None:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)

    from google.genai import types

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ExplanationSchema,
                temperature=0.2,
            ),
        )
    except Exception as exc:
        logger.error(f"Gemini API call failed: {exc}")
        from app.core.ai_errors import AIQuotaExceededError, is_ai_quota_error, sanitize_ai_error
        if is_ai_quota_error(exc):
            raise AIQuotaExceededError() from exc
        raise ExplainerError(sanitize_ai_error(exc)) from exc

    raw_text = getattr(response, "text", "") or ""
    try:
        data = json.loads(raw_text)
        explanation = data.get("explanation", "").strip() or raw_text.strip()
        remediation = data.get("remediation")
        if remediation:
            remediation = str(remediation).strip()
    except Exception:
        explanation = raw_text.strip()
        remediation = None

    return FindingExplanationResponse(
        finding_id=finding.id,
        explanation=explanation,
        remediation=remediation,
    )

