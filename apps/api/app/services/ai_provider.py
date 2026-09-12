"""Centralized AI Provider service for CodeLens.

Orchestrates primary Cloud AI (Google Gemini) and local Ollama fallback:
- In hybrid mode (default), attempts Cloud AI first.
- Automatically falls back to Ollama upon transient cloud failures:
  rate limit / quota exhaustion (429), timeouts, network connection failures,
  or temporary service outages (502/503/504).
- Strips markdown JSON fences safely before Pydantic/JSON validation.
- Preserves all response schemas and prevents provider-specific error leakage.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.ai_errors import (
    AI_UNAVAILABLE_MESSAGE,
    AIQuotaExceededError,
    AIUnavailableError,
    is_ai_quota_error,
    is_transient_cloud_error,
    sanitize_ai_error,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def strip_markdown_json_fences(raw_text: str) -> str:
    """Safely strip markdown code fences (```json ... ``` or ``` ... ```) from text.

    Also handles leading or trailing explanatory text outside the code block.
    """
    text = (raw_text or "").strip()
    if not text:
        return "{}"

    # If wrapped in standard triple backticks
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence line (e.g. ```json or ```)
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove closing fence line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # If there is markdown fenced block embedded inside text
    fence_pattern = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_pattern:
        candidate = fence_pattern.group(1).strip()
        if candidate.startswith(("{", "[")):
            return candidate

    # If text is surrounded by non-JSON preamble/postamble, extract the outer JSON object or array
    if not text.startswith(("{", "[")):
        json_obj_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if json_obj_match:
            return json_obj_match.group(1).strip()

    return text


def call_cloud_ai(
    prompt: str,
    schema: type[BaseModel] | None = None,
    temperature: float = 0.2,
    client: Any = None,
) -> str:
    """Call Google Gemini cloud AI and return raw text response."""
    if not settings.gemini_api_key and client is None:
        raise AIUnavailableError("GEMINI_API_KEY is not configured")

    gemini_client = client
    if gemini_client is None:
        from google import genai
        gemini_client = genai.Client(api_key=settings.gemini_api_key)

    from google.genai import types

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
    }
    if schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = schema

    response = gemini_client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return getattr(response, "text", "") or ""


def call_ollama(
    prompt: str,
    schema: type[BaseModel] | None = None,
    temperature: float = 0.2,
    base_url: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
) -> str:
    """Call local Ollama HTTP API and return raw response text."""
    endpoint_base = (base_url or settings.ollama_base_url or "http://localhost:11434").rstrip("/")
    endpoint_url = f"{endpoint_base}/api/generate"
    model_name = model or settings.ollama_model or "qwen2.5-coder:7b"
    req_timeout = timeout or settings.ollama_timeout or 60

    payload: dict[str, Any] = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
        },
    }

    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint_url,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "CodeLens-App",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=req_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return str(data.get("response", ""))
    except urllib.error.HTTPError as err:
        err_body = ""
        try:
            err_body = err.read().decode("utf-8")
        except Exception:
            pass
        raise AIUnavailableError(f"Ollama returned HTTP {err.code}: {err.reason} {err_body}".strip()) from err
    except urllib.error.URLError as err:
        raise AIUnavailableError(f"Failed to reach local Ollama: {err.reason}") from err
    except TimeoutError as err:
        raise AIUnavailableError("Ollama request timed out.") from err


def generate_ai_response(
    prompt: str,
    schema: type[BaseModel] | None = None,
    temperature: float = 0.2,
    cloud_client: Any = None,
    force_provider: str | None = None,
) -> tuple[str, str]:
    """Generate AI response with automatic Cloud AI -> Ollama fallback.

    Args:
        prompt: Prompt string sent to the model.
        schema: Optional Pydantic BaseModel schema class for JSON structured mode.
        temperature: Sampling temperature (default: 0.2).
        cloud_client: Optional pre-configured or mock cloud client.
        force_provider: Optional override ("cloud_only", "ollama_only", "hybrid").

    Returns:
        tuple of (raw_text_response, provider_name_used)

    Raises:
        AIQuotaExceededError: When quota is exceeded and fallback is not available or failed.
        AIUnavailableError: When AI services fail and cannot fulfill request.
    """
    mode = force_provider or settings.ai_provider_mode or "hybrid"

    # Mode 1: Ollama only
    if mode == "ollama_only":
        try:
            raw_text = call_ollama(prompt, schema=schema, temperature=temperature)
            return raw_text, "ollama"
        except Exception as exc:
            logger.error(f"Ollama execution failed: {exc}")
            raise AIUnavailableError(sanitize_ai_error(exc)) from exc

    # Mode 2: Hybrid (default) or Cloud-only
    cloud_error: Exception | None = None

    if settings.gemini_api_key or cloud_client is not None:
        try:
            raw_text = call_cloud_ai(prompt, schema=schema, temperature=temperature, client=cloud_client)
            return raw_text, "cloud"
        except Exception as exc:
            cloud_error = exc
            logger.warning(f"Cloud AI call failed: {exc}")
            # If not hybrid, or if error is not transient, do not fall back
            if mode != "hybrid" or not is_transient_cloud_error(exc):
                if is_ai_quota_error(exc):
                    raise AIQuotaExceededError() from exc
                raise
    else:
        # No cloud API key configured
        if mode != "hybrid":
            raise AIUnavailableError("GEMINI_API_KEY is not configured")
        cloud_error = AIUnavailableError("GEMINI_API_KEY is not configured")

    # If we reached here, cloud AI failed with a transient error or was not configured
    # Attempt local Ollama fallback
    logger.info("Attempting automatic fallback to local Ollama...")
    try:
        raw_text = call_ollama(prompt, schema=schema, temperature=temperature)
        return raw_text, "ollama"
    except Exception as ollama_exc:
        logger.error(f"Ollama fallback failed: {ollama_exc}")
        # Both failed: return provider-neutral error
        if cloud_error and is_ai_quota_error(cloud_error):
            raise AIQuotaExceededError() from cloud_error
        raise AIUnavailableError(sanitize_ai_error(cloud_error or ollama_exc)) from (cloud_error or ollama_exc)


def generate_structured(
    prompt: str,
    schema: type[T],
    temperature: float = 0.2,
    cloud_client: Any = None,
    force_provider: str | None = None,
) -> tuple[T, str]:
    """Generate structured response matching Pydantic schema using Cloud AI with Ollama fallback.

    Args:
        prompt: Prompt string sent to the model.
        schema: Target Pydantic BaseModel schema class.
        temperature: Sampling temperature (default: 0.2).
        cloud_client: Optional pre-configured or mock cloud client.
        force_provider: Optional override ("cloud_only", "ollama_only", "hybrid").

    Returns:
        tuple of (validated_schema_instance, provider_name_used)
    """
    raw_text, provider = generate_ai_response(
        prompt=prompt,
        schema=schema,
        temperature=temperature,
        cloud_client=cloud_client,
        force_provider=force_provider,
    )
    cleaned = strip_markdown_json_fences(raw_text)
    data = json.loads(cleaned)
    return _validate_schema(schema, data), provider


def _validate_schema(schema: type[T], data: dict[str, Any]) -> T:
    """Validate data against Pydantic schema supporting both v1 and v2 methods."""
    if hasattr(schema, "model_validate"):
        return schema.model_validate(data)
    return schema(**data)
