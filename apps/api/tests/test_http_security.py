
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings

client = TestClient(app)

def test_request_body_too_large():
    """Request body exceeding 1MB is rejected with 413."""
    large_payload = b"a" * (1_000_000 + 1)
    response = client.post("/auth/login", content=large_payload, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["code"] == "REQUEST_BODY_TOO_LARGE"

def test_normal_request_under_limit():
    """Normal request under the limit works normally."""
    response = client.post("/auth/login", json={"email": "test@example.com", "password": "wrong"})
    assert response.status_code == 401  # Normal authentication failure, meaning body passed

def test_security_headers_exist():
    """Security headers are present in responses."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers.get("permissions-policy", "")

def test_hsts_behavior_https_only():
    """HSTS is only included on secure (HTTPS) requests."""
    # HTTP request
    http_client = TestClient(app, base_url="http://localhost")
    response_http = http_client.get("/health")
    assert "strict-transport-security" not in response_http.headers

    # HTTPS request
    https_client = TestClient(app, base_url="https://codelens.app")
    response_https = https_client.get("/health")
    assert "strict-transport-security" in response_https.headers
    assert "max-age=31536000" in response_https.headers.get("strict-transport-security")

def test_cors_allowed_origin():
    """CORS allows the configured frontend origin."""
    origin = settings.frontend_url
    response = client.options(
        "/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST"
        }
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin

def test_cors_rejected_origin():
    """CORS rejects arbitrary origins."""
    response = client.options(
        "/auth/login",
        headers={
            "Origin": "http://evil.com",
            "Access-Control-Request-Method": "POST"
        }
    )
    assert response.status_code == 400
    assert "Disallowed CORS origin" in response.text or response.headers.get("access-control-allow-origin") is None

def test_cors_no_wildcard_with_credentials():
    """Credentialed requests do not use wildcard origins."""
    origin = settings.frontend_url
    response = client.options(
        "/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST"
        }
    )
    assert response.headers.get("access-control-allow-origin") != "*"
    assert response.headers.get("access-control-allow-credentials") == "true"

def test_auth_cache_control():
    """Authentication endpoints set Cache-Control: no-store."""
    response = client.post("/auth/login", json={"email": "test@example.com", "password": "wrong"})
    assert response.headers.get("cache-control") == "no-store"

def test_health_accessible():
    """Health endpoint remains accessible and lightweight."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "codelens"}

