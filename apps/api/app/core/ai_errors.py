"""Standardized AI error handling and sanitization for CodeLens.

Provides application-level handling for AI quota/rate-limit failures and ensures
provider-specific details (model names, internal metrics, API keys, URLs) never
leak to the client.
"""

from __future__ import annotations

import re
from fastapi import HTTPException, status

AI_QUOTA_CODE = "AI_QUOTA_EXCEEDED"
AI_QUOTA_MESSAGE = (
    "AI quota temporarily exhausted. CodeLens has reached its current AI usage "
    "limit. Static analysis is still available. Please try again later."
)

AI_UNAVAILABLE_CODE = "AI_UNAVAILABLE"
AI_UNAVAILABLE_MESSAGE = (
    "AI service is temporarily unavailable. Static analysis is still available. "
    "Please try again later."
)


class AIQuotaExceededError(Exception):
    """Raised when the AI provider rate limit or quota is exhausted."""

    def __init__(
        self,
        message: str = AI_QUOTA_MESSAGE,
        code: str = AI_QUOTA_CODE,
    ):
        super().__init__(message)
        self.message = message
        self.code = code


def is_ai_quota_error(exc: Exception | str) -> bool:
    """Detect if an exception or error string represents an AI quota/rate-limit failure."""
    if isinstance(exc, AIQuotaExceededError):
        return True

    # Check status attributes if available (e.g. Google API ClientError, HTTPError)
    for attr in ("code", "status_code", "http_status"):
        val = getattr(exc, attr, None)
        if val == 429:
            return True

    # Check exception class name
    cls_name = exc.__class__.__name__.lower()
    if "rate" in cls_name and "limit" in cls_name:
        return True
    if "resourceexhausted" in cls_name:
        return True

    msg = str(exc).lower()
    if "resource_exhausted" in msg or "resourceexhausted" in msg:
        return True
    if "quota" in msg:
        return True
    if "rate limit" in msg or "rate_limit" in msg or "ratelimit" in msg:
        return True
    if "429" in msg:
        return True
    if "too many requests" in msg:
        return True

    return False


# Provider-specific sensitive patterns that must not leak to the frontend
_PROVIDER_PATTERNS = [
    re.compile(r"gemini[-\w.]*", re.IGNORECASE),
    re.compile(r"google[-\w.]*", re.IGNORECASE),
    re.compile(r"generativelanguage\.googleapis\.com[^\s]*", re.IGNORECASE),
    re.compile(r"api[_-]?key[^\s,;]*", re.IGNORECASE),
    re.compile(r"resource_exhausted", re.IGNORECASE),
    re.compile(r"quotaId[^\s,;]*", re.IGNORECASE),
    re.compile(r"quotaMetric[^\s,;]*", re.IGNORECASE),
    re.compile(r"https?://[^\s]+", re.IGNORECASE),
]


def sanitize_ai_error(exc: Exception | str) -> str:
    """Sanitize any AI provider error to avoid leaking provider details or internal metrics.

    If the error message contains provider-specific details (Gemini, Google, quotaId, URLs),
    returns a friendly generic AI unavailable message. Otherwise preserves clean messages.
    """
    raw = str(exc).strip()
    for pattern in _PROVIDER_PATTERNS:
        if pattern.search(raw):
            return AI_UNAVAILABLE_MESSAGE
    return raw or AI_UNAVAILABLE_MESSAGE

