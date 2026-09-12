git diff --stat"""Tests for centralized AI Provider and automatic Cloud AI -> Ollama fallback.

Verifies:
1. Cloud AI succeeds -> Ollama is NOT called.
2. Cloud AI returns 429 (quota exceeded) -> Ollama fallback is called and succeeds.
3. Cloud AI connection error or timeout -> Ollama fallback is called and succeeds.
4. Cloud AI fails and Ollama also fails -> raises clean AIQuotaExceededError or AIUnavailableError.
5. Ollama returns valid structured JSON.
6. Ollama markdown-fenced JSON (```json ... ```) is parsed correctly.
7. Services continue returning exact response schemas via generate_structured.
8. Provider names and sensitive details are never leaked in error messages.
9. Mode overrides (ollama_only, cloud_only, hybrid) are respected.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from app.core.ai_errors import (
    AI_QUOTA_CODE,
    AI_QUOTA_MESSAGE,
    AI_UNAVAILABLE_CODE,
    AI_UNAVAILABLE_MESSAGE,
    AIQuotaExceededError,
    AIUnavailableError,
    is_ai_quota_error,
    is_transient_cloud_error,
    sanitize_ai_error,
)
from app.core.config import settings
from app.services.ai_provider import (
    call_cloud_ai,
    call_ollama,
    generate_ai_response,
    generate_structured,
    strip_markdown_json_fences,
)


class SampleSchema(BaseModel):
    title: str
    score: int


class TestAIProviderFallback(unittest.TestCase):
    """Unit tests for centralized hybrid AI provider fallback logic."""

    def setUp(self):
        self.patcher = patch.object(settings, "gemini_api_key", "mock-cloud-key")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_cloud_ai_succeeds_ollama_not_called(self, mock_cloud, mock_ollama):
        """When Cloud AI succeeds, Ollama must not be called."""
        mock_cloud.return_value = '{"title": "test", "score": 100}'

        text, provider = generate_ai_response(
            prompt="Analyze this code",
            schema=SampleSchema,
        )

        self.assertEqual(provider, "cloud")
        self.assertIn("score", text)
        mock_cloud.assert_called_once()
        mock_ollama.assert_not_called()

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_cloud_ai_429_quota_falls_back_to_ollama(self, mock_cloud, mock_ollama):
        """When Cloud AI returns 429 quota error, it automatically falls back to Ollama."""
        mock_cloud.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded for metric")
        mock_ollama.return_value = '{"title": "from_ollama", "score": 95}'

        text, provider = generate_ai_response(
            prompt="Analyze this code",
            schema=SampleSchema,
        )

        self.assertEqual(provider, "ollama")
        self.assertIn("from_ollama", text)
        mock_cloud.assert_called_once()
        mock_ollama.assert_called_once()

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_cloud_ai_timeout_falls_back_to_ollama(self, mock_cloud, mock_ollama):
        """When Cloud AI encounters a timeout, it automatically falls back to Ollama."""
        mock_cloud.side_effect = TimeoutError("Request timed out after 30s")
        mock_ollama.return_value = '{"title": "ollama_timeout_fallback", "score": 80}'

        text, provider = generate_ai_response(
            prompt="Analyze this code",
            schema=SampleSchema,
        )

        self.assertEqual(provider, "ollama")
        self.assertIn("ollama_timeout_fallback", text)
        mock_cloud.assert_called_once()
        mock_ollama.assert_called_once()

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_cloud_ai_connection_error_falls_back_to_ollama(self, mock_cloud, mock_ollama):
        """When Cloud AI encounters a connection error, it automatically falls back to Ollama."""
        mock_cloud.side_effect = ConnectionError("Failed to connect to host")
        mock_ollama.return_value = '{"title": "ollama_conn_fallback", "score": 85}'

        text, provider = generate_ai_response(
            prompt="Analyze this code",
            schema=SampleSchema,
        )

        self.assertEqual(provider, "ollama")
        self.assertIn("ollama_conn_fallback", text)
        mock_cloud.assert_called_once()
        mock_ollama.assert_called_once()

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_both_fail_with_quota_raises_clean_ai_quota_error(self, mock_cloud, mock_ollama):
        """When Cloud AI fails with 429 and Ollama also fails, raise AIQuotaExceededError."""
        mock_cloud.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded")
        mock_ollama.side_effect = RuntimeError("Ollama connection refused")

        with self.assertRaises(AIQuotaExceededError) as ctx:
            generate_ai_response(
                prompt="Analyze this code",
                schema=SampleSchema,
            )

        self.assertEqual(ctx.exception.code, AI_QUOTA_CODE)
        self.assertEqual(ctx.exception.message, AI_QUOTA_MESSAGE)

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_both_fail_with_network_raises_clean_ai_unavailable_error(self, mock_cloud, mock_ollama):
        """When Cloud AI has network error and Ollama also fails, raise AIUnavailableError."""
        mock_cloud.side_effect = ConnectionError("Network unreachable")
        mock_ollama.side_effect = RuntimeError("Ollama daemon is down")

        with self.assertRaises(AIUnavailableError) as ctx:
            generate_ai_response(
                prompt="Analyze this code",
                schema=SampleSchema,
            )

        self.assertEqual(ctx.exception.code, AI_UNAVAILABLE_CODE)

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_non_transient_cloud_error_does_not_fall_back(self, mock_cloud, mock_ollama):
        """When Cloud AI fails with a non-transient error (e.g. invalid syntax), do not fallback."""
        mock_cloud.side_effect = ValueError("Invalid prompt parameters")

        with self.assertRaises(ValueError):
            generate_ai_response(
                prompt="Analyze this code",
                schema=SampleSchema,
            )

        mock_cloud.assert_called_once()
        mock_ollama.assert_not_called()

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_force_ollama_only_skips_cloud(self, mock_cloud, mock_ollama):
        """force_provider='ollama_only' skips Cloud AI entirely."""
        mock_ollama.return_value = '{"title": "direct_ollama", "score": 90}'

        text, provider = generate_ai_response(
            prompt="Analyze this code",
            force_provider="ollama_only",
        )

        self.assertEqual(provider, "ollama")
        self.assertIn("direct_ollama", text)
        mock_cloud.assert_not_called()
        mock_ollama.assert_called_once()

    @patch("app.services.ai_provider.call_ollama")
    @patch("app.services.ai_provider.call_cloud_ai")
    def test_force_cloud_only_never_calls_ollama(self, mock_cloud, mock_ollama):
        """force_provider='cloud_only' fails immediately on cloud failure without Ollama."""
        mock_cloud.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded")

        with self.assertRaises(AIQuotaExceededError):
            generate_ai_response(
                prompt="Analyze this code",
                schema=SampleSchema,
                force_provider="cloud_only",
            )

        mock_cloud.assert_called_once()
        mock_ollama.assert_not_called()


class TestMarkdownFencesAndStructuredOutput(unittest.TestCase):
    """Unit tests for JSON fence stripping and structured output parsing."""

    def test_strip_markdown_json_fences_clean_json(self):
        clean = '{"title": "foo", "score": 10}'
        self.assertEqual(strip_markdown_json_fences(clean), clean)

    def test_strip_markdown_json_fences_with_json_tag(self):
        fenced = '```json\n{"title": "foo", "score": 10}\n```'
        result = strip_markdown_json_fences(fenced)
        self.assertEqual(json.loads(result), {"title": "foo", "score": 10})

    def test_strip_markdown_json_fences_without_json_tag(self):
        fenced = '```\n{"title": "foo", "score": 10}\n```'
        result = strip_markdown_json_fences(fenced)
        self.assertEqual(json.loads(result), {"title": "foo", "score": 10})

    def test_strip_markdown_json_fences_with_preamble_and_postamble(self):
        text = 'Here is your structured result:\n```json\n{"title": "foo", "score": 10}\n```\nHope this helps!'
        result = strip_markdown_json_fences(text)
        self.assertEqual(json.loads(result), {"title": "foo", "score": 10})

    def test_strip_markdown_json_fences_empty(self):
        self.assertEqual(strip_markdown_json_fences(""), "{}")
        self.assertEqual(strip_markdown_json_fences("   "), "{}")

    @patch("app.services.ai_provider.generate_ai_response")
    def test_generate_structured_returns_validated_pydantic_model(self, mock_generate):
        """generate_structured parses raw text into the requested Pydantic schema."""
        mock_generate.return_value = (
            '```json\n{"title": "Validated Result", "score": 42}\n```',
            "ollama",
        )

        model, provider = generate_structured(
            prompt="Generate sample data",
            schema=SampleSchema,
        )

        self.assertIsInstance(model, SampleSchema)
        self.assertEqual(model.title, "Validated Result")
        self.assertEqual(model.score, 42)
        self.assertEqual(provider, "ollama")


class TestProviderSanitization(unittest.TestCase):
    """Unit tests ensuring zero leakage of provider names, keys, and internal URLs."""

    def test_sanitize_gemini_leakage(self):
        msg = "Error talking to gemini-2.5-flash at generativelanguage.googleapis.com"
        sanitized = sanitize_ai_error(msg)
        self.assertNotIn("gemini", sanitized.lower())
        self.assertNotIn("googleapis", sanitized.lower())
        self.assertEqual(sanitized, AI_UNAVAILABLE_MESSAGE)

    def test_sanitize_ollama_leakage(self):
        msg = "Failed to connect to http://localhost:11434/api/generate model qwen2.5-coder:7b"
        sanitized = sanitize_ai_error(msg)
        self.assertNotIn("ollama", sanitized.lower())
        self.assertNotIn("qwen", sanitized.lower())
        self.assertNotIn("11434", sanitized.lower())
        self.assertNotIn("http://", sanitized.lower())
        self.assertEqual(sanitized, AI_UNAVAILABLE_MESSAGE)

    def test_sanitize_api_key_leakage(self):
        msg = "Invalid api_key=AIzaSyD-123456789"
        sanitized = sanitize_ai_error(msg)
        self.assertNotIn("api_key", sanitized.lower())
        self.assertNotIn("AIzaSy", sanitized)
        self.assertEqual(sanitized, AI_UNAVAILABLE_MESSAGE)

    def test_transient_error_detection(self):
        self.assertTrue(is_transient_cloud_error(TimeoutError("Connection timed out")))
        self.assertTrue(is_transient_cloud_error(ConnectionError("Connection refused")))
        self.assertTrue(is_transient_cloud_error(RuntimeError("503 Service Unavailable")))
        self.assertTrue(is_transient_cloud_error(RuntimeError("502 Bad Gateway")))
        self.assertTrue(is_transient_cloud_error(RuntimeError("429 RESOURCE_EXHAUSTED")))
        self.assertFalse(is_transient_cloud_error(ValueError("Invalid syntax")))

