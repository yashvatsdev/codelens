"""AI-powered code fix generation for Pull Request review findings.

Allows developers to generate proposed AI fixes directly from a PR key finding,
inspect the original code vs fixed code and unified diff, and preview the
resulting code in memory.

Nothing is written to GitHub, the repository, files on disk, or the database.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from app.core.config import settings
from app.schemas.finding import PRFindingFixResponse
from app.services.explainer import extract_code_context
from app.services.fixer import compute_resulting_code, compute_unified_diff

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AIPRFixerError(Exception):
    """Base exception for AI PR fixer service errors."""
    pass


class GeminiNotConfiguredError(AIPRFixerError):
    """Raised when GEMINI_API_KEY is not configured."""
    pass


# ---------------------------------------------------------------------------
# Gemini structured-output schema
# ---------------------------------------------------------------------------

class PRFixSchema(BaseModel):
    """Structured response schema for PR finding fix generation."""
    explanation: str
    original_code: str
    fixed_code: str
    diff: str | None = None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_pr_fixer_prompt(
    file_path: str,
    line_number: int,
    issue: str,
    severity: str,
    category: str,
    message: str,
    code_context: str,
) -> str:
    """Construct a clear, structured prompt for Gemini to fix a PR finding."""
    return f"""You are an expert software engineer and automated code remediation assistant.
Analyze the following code finding from a pull request review and generate a precise, minimal, and safe code fix.

PR FINDING DETAILS:
- File Path: {file_path}
- Line Number: {line_number}
- Issue: {issue}
- Severity: {severity}
- Category: {category}
- Message: {message}

SOURCE CODE CONTEXT:
```
{code_context}
```

Instructions:
1. Explain the proposed fix clearly and concisely, explaining why it resolves the issue without introducing regressions or changing unrelated behavior.
2. Provide the original code snippet that needs to be replaced. Ensure the original snippet matches what appears in the source context.
3. Provide the fixed replacement code snippet. If the line should be deleted, provide an empty string.
4. Provide a unified diff/patch showing the change.

Return your response in valid JSON matching this schema:
{{
  "explanation": "<Clear description of the fix and rationale>",
  "original_code": "<The original code snippet being replaced>",
  "fixed_code": "<The replacement fixed code snippet>",
  "diff": "<Unified diff patch string, or null>"
}}
"""


# ---------------------------------------------------------------------------
# Core service function
# ---------------------------------------------------------------------------

def generate_pr_finding_fix(
    file_contents: dict[str, str],
    file_path: str,
    line_number: int,
    issue: str,
    severity: str,
    category: str,
    message: str,
    gemini_client: Any = None,
) -> PRFindingFixResponse:
    """Generate an AI-powered code fix for a PR finding using Google Gemini.

    Operates strictly in memory using the PR-version file content.
    Does NOT modify database records or GitHub pull requests.

    Args:
        file_contents: Mapping of file paths to their PR-version source content.
        file_path: Relative path of the file containing the finding.
        line_number: 1-indexed line number of the finding.
        issue: Brief description of the issue.
        severity: Finding severity level.
        category: Finding category.
        message: Detailed finding message.
        gemini_client: Optional mock or preconfigured Gemini client.

    Returns:
        PRFindingFixResponse with explanation, original_code, fixed_code, diff, and resulting_code.

    Raises:
        GeminiNotConfiguredError: If GEMINI_API_KEY is missing and client not passed.
        AIPRFixerError: If the Gemini call fails.
    """
    if not settings.gemini_api_key and gemini_client is None:
        raise GeminiNotConfiguredError("GEMINI_API_KEY is not configured")

    source_content = file_contents.get(file_path, "")

    code_context = extract_code_context(
        content=source_content,
        line_number=line_number,
        window=10,
    )

    prompt = build_pr_fixer_prompt(
        file_path=file_path,
        line_number=line_number,
        issue=issue,
        severity=severity,
        category=category,
        message=message,
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
                response_schema=PRFixSchema,
                temperature=0.2,
            ),
        )
    except Exception as exc:
        logger.error(f"Gemini API call for PR fix generation failed: {exc}")
        raise AIPRFixerError(f"Gemini API error: {exc}") from exc

    raw_text = getattr(response, "text", "") or ""

    explanation = ""
    original_code = ""
    fixed_code = ""
    diff = None

    try:
        text_to_parse = raw_text.strip()
        # Strip markdown code fences if Gemini returned ```json ... ```
        if text_to_parse.startswith("```"):
            lines = text_to_parse.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text_to_parse = "\n".join(lines).strip()

        data = json.loads(text_to_parse)
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
            file_path=file_path or "source_file",
        )

    # Compute preview of resulting code after applying the fix
    resulting_code = compute_resulting_code(
        source_content=source_content,
        original_code=original_code,
        fixed_code=fixed_code,
        line_number=line_number,
        window=5,
    )

    return PRFindingFixResponse(
        file_path=file_path,
        line_number=line_number,
        explanation=explanation,
        original_code=original_code,
        fixed_code=fixed_code,
        diff=diff,
        resulting_code=resulting_code,
    )
