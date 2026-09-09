import json
import logging
from typing import Any
from pydantic import BaseModel

from app.core.config import settings
from app.models.finding import Finding
from app.schemas.finding import FindingTestResponse
from app.services.explainer import extract_code_context

logger = logging.getLogger(__name__)


class TestGeneratorError(Exception):
    """Base exception for test generator service errors."""
    __test__ = False


class GeminiNotConfiguredError(TestGeneratorError):
    """Raised when GEMINI_API_KEY is not configured."""
    pass


class FindingNotFoundError(TestGeneratorError):
    """Raised when finding is not found or does not belong to the repository."""
    pass


class SourceFileNotFoundError(TestGeneratorError):
    """Raised when source file cannot be found."""
    pass


class TestSchema(BaseModel):
    __test__ = False
    test_framework: str
    test_file: str
    test_code: str
    explanation: str


def build_test_prompt(
    rule_id: str,
    severity: str,
    category: str,
    message: str,
    file_path: str | None,
    line_number: int | None,
    code_context: str,
) -> str:
    """Build a concise, structured prompt for generating a unit test using Gemini."""
    return f"""You are an expert test engineer and software quality assistant.
Analyze the following static code analysis finding and generate a comprehensive, runnable unit test that tests the behavior and verifies the fix or edge case.

FINDING METADATA:
- Rule ID: {rule_id}
- Severity: {severity}
- Category: {category}
- Message: {message}
- File Path: {file_path or 'unknown'}
- Line Number: {line_number if line_number is not None else 'N/A'}

RELEVANT SOURCE CODE CONTEXT:
```
{code_context}
```

Instructions:
1. Choose the standard testing framework appropriate for the file language (e.g. pytest / unittest for Python, jest / vitest / mocha for JavaScript/TypeScript, go test for Go).
2. Suggest a suitable test file path (e.g. tests/test_<name>.py or __tests__/<name>.test.js).
3. Provide clean, runnable test code with assertions that target the specific issue.
4. Provide a clear explanation of what the test verifies.

Return your answer in valid JSON matching this schema:
{{
  "test_framework": "<e.g. pytest, unittest, jest>",
  "test_file": "<Suggested test file path, e.g. tests/test_example.py>",
  "test_code": "<The complete runnable test code>",
  "explanation": "<Explanation of what the test tests and how it catches the issue>"
}}
"""


def generate_test(
    finding: Finding,
    source_content: str,
    gemini_client: Any = None,
) -> FindingTestResponse:
    """Generate an AI-powered unit test for a finding using Google Gemini.

    Args:
        finding: Finding model instance.
        source_content: Full source code of the file containing the finding.
        gemini_client: Optional mock or preconfigured Gemini client.

    Returns:
        FindingTestResponse with finding_id, test_framework, test_file, test_code, and explanation.

    Raises:
        GeminiNotConfiguredError: If GEMINI_API_KEY is not configured.
        TestGeneratorError: If Gemini API call fails.
    """
    if not settings.gemini_api_key and gemini_client is None:
        raise GeminiNotConfiguredError("GEMINI_API_KEY is not configured")

    code_context = extract_code_context(
        content=source_content,
        line_number=finding.line_number,
        window=10,
    )

    prompt = build_test_prompt(
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
                response_schema=TestSchema,
                temperature=0.2,
            ),
        )
    except Exception as exc:
        logger.error(f"Gemini API call for test generation failed: {exc}")
        from app.core.ai_errors import AIQuotaExceededError, is_ai_quota_error, sanitize_ai_error
        if is_ai_quota_error(exc):
            raise AIQuotaExceededError() from exc
        raise TestGeneratorError(sanitize_ai_error(exc)) from exc

    raw_text = getattr(response, "text", "") or ""
    test_framework = "pytest" if (finding.file_path and finding.file_path.endswith(".py")) else "jest"
    test_file = f"tests/test_{finding.file_path.split('/')[-1]}" if finding.file_path else "tests/test_finding.py"
    test_code = ""
    explanation = ""

    try:
        data = json.loads(raw_text)
        test_framework = str(data.get("test_framework", test_framework)).strip()
        test_file = str(data.get("test_file", test_file)).strip()
        test_code = str(data.get("test_code", ""))
        explanation = str(data.get("explanation", "")).strip()
    except Exception:
        explanation = raw_text.strip()
        test_code = raw_text.strip()

    return FindingTestResponse(
        finding_id=finding.id,
        test_framework=test_framework,
        test_file=test_file,
        test_code=test_code,
        explanation=explanation,
    )
