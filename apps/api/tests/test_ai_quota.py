
import pytest
from unittest.mock import patch, MagicMock

from app.services.ai_provider import (
    MAX_AI_CONTEXT_CHARS,
    MAX_AI_OUTPUT_TOKENS,
    generate_ai_response,
    call_cloud_ai,
    call_ollama,
)
from app.core.ai_errors import AIQuotaExceededError, AIUnavailableError


def test_ai_context_length_limit():
    """AI context above the configured maximum is safely rejected."""
    long_prompt = "a" * (MAX_AI_CONTEXT_CHARS + 1)
    
    with pytest.raises(AIQuotaExceededError) as exc:
        generate_ai_response(long_prompt)
        
    assert "exceeds maximum allowed length" in str(exc.value.message)


def test_ai_output_capped():
    """Provider calls enforce output size limits."""
    # Patch google.genai.Client directly
    with patch("google.genai.Client") as mock_client_class:
        mock_instance = MagicMock()
        mock_client_class.return_value = mock_instance
        
        with patch("app.services.ai_provider.settings.gemini_api_key", "fake-key"):
            call_cloud_ai("test prompt", temperature=0.5)
            
            mock_instance.models.generate_content.assert_called_once()
            call_args = mock_instance.models.generate_content.call_args[1]
            config = call_args["config"]
            assert config.max_output_tokens == MAX_AI_OUTPUT_TOKENS


def test_ollama_output_capped():
    """Ollama fallback enforces output size limits (num_predict)."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"response": "test output"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response
        
        call_ollama("test prompt")
        
        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args[0][0]
        import json
        body = json.loads(request.data.decode("utf-8"))
        assert body["options"]["num_predict"] == MAX_AI_OUTPUT_TOKENS


def test_cloud_fallback_does_not_multiply_requests():
    """A failed cloud request does exactly ONE fallback to Ollama, not a retry loop."""
    with patch("app.services.ai_provider.call_cloud_ai") as mock_cloud, \
         patch("app.services.ai_provider.call_ollama") as mock_ollama:
        
        # Simulate transient error on cloud (e.g., HTTP 503 equivalent)
        class FakeTransientError(Exception):
            status_code = 503
            
        mock_cloud.side_effect = FakeTransientError("Cloud transient failure")
        mock_ollama.return_value = "Ollama response"
        
        with patch("app.services.ai_provider.settings.gemini_api_key", "fake-key"), \
             patch("app.services.ai_provider.settings.ai_provider_mode", "hybrid"):
            
            raw_text, provider = generate_ai_response("short prompt")
            
            assert raw_text == "Ollama response"
            assert provider == "ollama"
            mock_cloud.assert_called_once()
            mock_ollama.assert_called_once()


def test_provider_timeout_handled():
    """Provider timeouts raise controlled AIUnavailableError without crashing."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = TimeoutError("Connection timed out")
        
        with pytest.raises(AIUnavailableError) as exc:
            call_ollama("test prompt")
            
        assert "timed out" in str(exc.value)

