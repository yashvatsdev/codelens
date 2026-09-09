import difflib
import json
import logging
from typing import Any
from pydantic import BaseModel

from app.core.config import settings
from app.models.finding import Finding
from app.schemas.finding import FindingFixResponse
from app.services.explainer import extract_code_context

logger = logging.getLogger(__name__)


class FixerError(Exception):
    """Base exception for fixer service errors."""
    pass


class GeminiNotConfiguredError(FixerError):
    """Raised when GEMINI_API_KEY is not configured."""
    pass


class FixSchema(BaseModel):
    explanation: str
    original_code: str
    fixed_code: str
    diff: str | None = None


def compute_unified_diff(original: str, fixed: str, file_path: str = "file") -> str:
    """Compute a standard unified diff between original and fixed code."""
    original_lines = original.splitlines(keepends=True)
    fixed_lines = fixed.splitlines(keepends=True)
    if original_lines and not original_lines[-1].endswith("\n"):
        original_lines[-1] += "\n"
    if fixed_lines and not fixed_lines[-1].endswith("\n"):
        fixed_lines[-1] += "\n"

    diff = list(
        difflib.unified_diff(
            original_lines,
            fixed_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )
    return "".join(diff)


def build_fixer_prompt(
    rule_id: str,
    severity: str,
    category: str,
    message: str,
    file_path: str | None,
    line_number: int | None,
    code_context: str,
) -> str:
    """Build a concise, structured prompt for generating a fix using Gemini."""
    return f"""You are an expert static code analysis and remediation assistant.
Analyze the following code finding and generate a precise, safe code fix.

FINDING METADATA:
- Rule ID: {rule_id}
- Severity: {severity}
- Category: {category}
- Message: {message}
- File Path: {file_path or 'unknown'}
- Line Number: {line_number if line_number is not None else 'N/A'}

RELEVANT CODE CONTEXT:
```
{code_context}
```

Instructions:
1. Explain the proposed fix and why this change resolves the issue without introducing regressions or side effects.
2. Provide the original code snippet that needs to be replaced.
3. Provide the fixed replacement code snippet.
4. Provide a unified diff/patch showing the replacement.

Return your answer in valid JSON matching this schema:
{{
  "explanation": "<Clear description of the change and why it fixes the issue>",
  "original_code": "<The original code snippet being replaced>",
  "fixed_code": "<The replacement fixed code snippet>",
  "diff": "<Unified diff patch string, or null>"
}}
"""


def generate_fix(
    finding: Finding,
    source_content: str,
    gemini_client: Any = None,
) -> FindingFixResponse:
    """Generate an AI-powered code fix for a finding using Google Gemini.

    Args:
        finding: Finding model instance.
        source_content: Full source code of the file containing the finding.
        gemini_client: Optional mock or preconfigured Gemini client.

    Returns:
        FindingFixResponse with explanation, original_code, fixed_code, and diff.

    Raises:
        GeminiNotConfiguredError: If GEMINI_API_KEY is not set.
        FixerError: If the Gemini call fails.
    """
    if not settings.gemini_api_key and gemini_client is None:
        raise GeminiNotConfiguredError("GEMINI_API_KEY is not configured")

    code_context = extract_code_context(
        content=source_content,
        line_number=finding.line_number,
        window=10,
    )

    prompt = build_fixer_prompt(
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
                response_schema=FixSchema,
                temperature=0.2,
            ),
        )
    except Exception as exc:
        logger.error(f"Gemini API call for fix generation failed: {exc}")
        raise FixerError(f"Gemini API error: {exc}") from exc

    raw_text = getattr(response, "text", "") or ""
    explanation = ""
    original_code = ""
    fixed_code = ""
    diff = None

    try:
        data = json.loads(raw_text)
        explanation = str(data.get("explanation", "")).strip()
        original_code = str(data.get("original_code", ""))
        fixed_code = str(data.get("fixed_code", ""))
        diff = data.get("diff")
        if diff:
            diff = str(diff)
    except Exception:
        explanation = raw_text.strip()

    # If diff was not provided or empty, compute unified diff from original and fixed code
    if not diff and (original_code or fixed_code):
        diff = compute_unified_diff(
            original=original_code,
            fixed=fixed_code,
            file_path=finding.file_path or "source_file",
        )

    return FindingFixResponse(
        finding_id=finding.id,
        explanation=explanation,
        original_code=original_code,
        fixed_code=fixed_code,
        diff=diff,
    )
