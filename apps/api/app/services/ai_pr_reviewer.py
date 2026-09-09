"""AI-powered Pull Request review service.

Takes the Phase 1 PR review results (static-analysis findings and source
context), sends them to Gemini for intelligent analysis, and returns a
structured AI review with risk assessment, prioritized findings, and
actionable recommendations.

Nothing is written to the database.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from app.core.config import settings
from app.services.explainer import extract_code_context
from app.services.pr_reviewer import PRFinding, PRReviewResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AIPRReviewerError(Exception):
    """Base exception for AI PR reviewer errors."""
    pass


class GeminiNotConfiguredError(AIPRReviewerError):
    """Raised when GEMINI_API_KEY is not configured."""
    pass


# ---------------------------------------------------------------------------
# Gemini structured-output schema
# ---------------------------------------------------------------------------

class _KeyFindingSchema(BaseModel):
    """Schema for a single key finding in the Gemini response."""
    file_path: str
    line_number: int | None = None
    severity: str
    category: str
    issue: str
    impact: str
    recommendation: str


class _AIPRReviewSchema(BaseModel):
    """Schema for the complete AI PR review Gemini response."""
    summary: str
    risk_level: str
    overall_assessment: str
    key_findings: list[_KeyFindingSchema] = []
    recommendations: list[str] = []


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

def _format_findings_for_prompt(findings: list[PRFinding]) -> str:
    """Format PR findings into a readable text block for the Gemini prompt."""
    if not findings:
        return "(No static-analysis issues detected)"

    lines: list[str] = []
    for i, f in enumerate(findings, 1):
        lines.append(
            f"{i}. [{f.severity.upper()}] {f.file_path}:{f.line_number or '?'} "
            f"— {f.message} (rule: {f.rule_id}, category: {f.category})"
        )
    return "\n".join(lines)


def _format_source_context(
    file_contents: dict[str, str],
    findings: list[PRFinding],
    context_window: int = 8,
    max_files: int = 15,
) -> str:
    """Extract source code context around findings for the Gemini prompt.

    Reuses the existing ``extract_code_context`` utility from the explainer
    service so that context formatting is consistent across CodeLens.
    """
    if not file_contents or not findings:
        return "(No source context available)"

    # Group finding line numbers by file path
    files_with_findings: dict[str, list[int | None]] = {}
    for f in findings:
        files_with_findings.setdefault(f.file_path, []).append(f.line_number)

    sections: list[str] = []
    for file_path, line_numbers in list(files_with_findings.items())[:max_files]:
        content = file_contents.get(file_path)
        if not content:
            continue

        seen_lines: set[int | None] = set()
        for line_num in line_numbers:
            if line_num in seen_lines:
                continue
            seen_lines.add(line_num)
            context = extract_code_context(content, line_num, window=context_window)
            sections.append(
                f"--- {file_path} (around line {line_num or 'start'}) ---\n{context}"
            )

    return "\n\n".join(sections) if sections else "(No source context available)"


def build_ai_pr_review_prompt(
    repo_full_name: str,
    pr_number: int,
    files_changed: int,
    files_analyzed: int,
    findings_text: str,
    source_context: str,
) -> str:
    """Build a structured prompt for the Gemini AI PR review."""
    return f"""You are a senior software engineer performing a thorough code review of a GitHub Pull Request.

PULL REQUEST CONTEXT:
- Repository: {repo_full_name}
- PR Number: #{pr_number}
- Files Changed: {files_changed}
- Files Analyzed by Static Analysis: {files_analyzed}

STATIC ANALYSIS FINDINGS:
{findings_text}

RELEVANT SOURCE CODE CONTEXT:
{source_context}

INSTRUCTIONS:
1. Analyze the static-analysis findings above and assess the overall risk of this PR.
2. Prioritize security and correctness issues above style issues.
3. For each significant finding, explain what is wrong, why it matters, and how to fix it.
4. Provide an overall risk level: "low", "medium", "high", or "critical".
5. Provide actionable recommendations prioritized by importance.
6. Do NOT invent issues that are not supported by the supplied findings or source context.
7. Do NOT fabricate file paths, line numbers, or vulnerabilities.
8. If there are no static-analysis findings, set risk_level to "low" and note that no issues were detected.
9. Be specific and actionable. Avoid generic filler advice.
10. If evidence is insufficient to determine impact, explicitly say so.

Return your answer in valid JSON matching this schema:
{{
  "summary": "<Brief summary of the PR review — 1-3 sentences>",
  "risk_level": "<low | medium | high | critical>",
  "overall_assessment": "<Detailed overall assessment of the PR quality and risk>",
  "key_findings": [
    {{
      "file_path": "<file path from the findings>",
      "line_number": <line number or null>,
      "severity": "<error | warning | info>",
      "category": "<security | bug | style | complexity | maintainability>",
      "issue": "<Clear description of what is wrong>",
      "impact": "<Why this matters and potential consequences>",
      "recommendation": "<Specific actionable fix recommendation>"
    }}
  ],
  "recommendations": [
    "<Prioritized actionable recommendation>"
  ]
}}
"""


# ---------------------------------------------------------------------------
# Main service function
# ---------------------------------------------------------------------------

def generate_ai_pr_review(
    review_result: PRReviewResult,
    repo_full_name: str,
    gemini_client: Any = None,
) -> dict:
    """Generate an AI-powered PR review using Google Gemini.

    Accepts the Phase 1 ``PRReviewResult`` (with static-analysis findings
    and fetched file contents), constructs a bounded prompt, calls Gemini,
    and parses the structured response.

    Args:
        review_result: The Phase 1 PR review result.
        repo_full_name: Full repository name (e.g. ``"owner/repo"``).
        gemini_client: Optional pre-configured or mock Gemini client.

    Returns:
        Dict with ``summary``, ``risk_level``, ``overall_assessment``,
        ``key_findings``, and ``recommendations``.

    Raises:
        GeminiNotConfiguredError: If ``GEMINI_API_KEY`` is not configured.
        AIPRReviewerError: If the Gemini API call fails.
    """
    if not settings.gemini_api_key and gemini_client is None:
        raise GeminiNotConfiguredError("GEMINI_API_KEY is not configured")

    findings_text = _format_findings_for_prompt(review_result.findings)
    source_context = _format_source_context(
        file_contents=review_result.file_contents,
        findings=review_result.findings,
    )

    prompt = build_ai_pr_review_prompt(
        repo_full_name=repo_full_name,
        pr_number=review_result.pull_request_number,
        files_changed=review_result.files_changed,
        files_analyzed=review_result.files_analyzed,
        findings_text=findings_text,
        source_context=source_context,
    )

    # Instantiate Gemini client (reusing existing config pattern)
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
                response_schema=_AIPRReviewSchema,
                temperature=0.2,
            ),
        )
    except Exception as exc:
        logger.error(f"Gemini API call for AI PR review failed: {exc}")
        raise AIPRReviewerError(f"Gemini API error: {exc}") from exc

    # ---- Safe response parsing ----
    raw_text = getattr(response, "text", "") or ""

    summary = "AI review completed."
    risk_level = "low"
    overall_assessment = ""
    key_findings: list[dict] = []
    recommendations: list[str] = []

    try:
        text_to_parse = raw_text.strip()

        # Strip markdown code fences if present
        if text_to_parse.startswith("```"):
            lines = text_to_parse.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text_to_parse = "\n".join(lines)

        data = json.loads(text_to_parse)
        summary = str(data.get("summary", summary)).strip()
        risk_level = str(data.get("risk_level", risk_level)).strip().lower()
        overall_assessment = str(data.get("overall_assessment", "")).strip()

        # Validate risk_level
        if risk_level not in ("low", "medium", "high", "critical"):
            risk_level = "medium"

        for kf in data.get("key_findings", []):
            if not isinstance(kf, dict):
                continue
            key_findings.append({
                "file_path": str(kf.get("file_path", "")),
                "line_number": kf.get("line_number"),
                "severity": str(kf.get("severity", "info")),
                "category": str(kf.get("category", "general")),
                "issue": str(kf.get("issue", "")),
                "impact": str(kf.get("impact", "")),
                "recommendation": str(kf.get("recommendation", "")),
            })

        for rec in data.get("recommendations", []):
            if isinstance(rec, str) and rec.strip():
                recommendations.append(rec.strip())

    except (json.JSONDecodeError, KeyError, TypeError):
        # Safe fallback: return basic review without crashing
        summary = raw_text.strip() if raw_text.strip() else "AI review could not produce structured output."
        risk_level = "low" if not review_result.findings else "medium"

    return {
        "summary": summary,
        "risk_level": risk_level,
        "overall_assessment": overall_assessment,
        "key_findings": key_findings,
        "recommendations": recommendations,
    }
