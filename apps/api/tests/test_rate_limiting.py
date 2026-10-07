"""Comprehensive tests for Phase 1 API rate limiting.

Tests cover:
- global default limit
- per-endpoint specific limits (login, signup, google login, github metadata,
  github connect, scan, ask, explain, fix, test-gen, ai-pr-review)
- 429 response format (safe message, correct status code)
- Retry-After header presence
- test isolation via reset_limiter() in conftest autouse fixture
- existing auth/repo/AI endpoints still function normally
- health endpoint is not blocked

Rate-limit semantics (FixedWindowRateLimiter):
  For a limit of N/period: requests 1..N are allowed; request N+1 returns 429.
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.rate_limit import reset_limiter, DEFAULT_LIMIT, ROUTE_LIMITS, _match_path
from app.api.deps import get_current_user
from tests.conftest import FAKE_USER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client():
    return TestClient(app, raise_server_exceptions=False)


def _hit(client, method, url, n, **kwargs):
    """Fire n requests and return all responses."""
    fn = getattr(client, method.lower())
    return [fn(url, **kwargs) for _ in range(n)]


def _make_github_metadata():
    """Return a valid GitHubRepoMetadata mock instance."""
    from app.services.github import GitHubRepoMetadata
    return GitHubRepoMetadata(
        owner="o",
        name="n",
        full_name="o/n",
        description=None,
        default_branch="main",
        url="https://github.com/o/n",
    )


# ---------------------------------------------------------------------------
# Base test class
# The conftest autouse sync_sequences fixture already calls reset_limiter()
# before every test. We also call it in setUp/tearDown for extra safety and
# to ensure the TestClient is created after the reset.
# ---------------------------------------------------------------------------

class RateLimitTestBase(unittest.TestCase):
    def setUp(self):
        reset_limiter()
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        self.client = _make_client()

    def tearDown(self):
        reset_limiter()
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Unit tests — path matching logic
# ---------------------------------------------------------------------------

class TestPathMatching(unittest.TestCase):
    """Verify _match_path returns correct limit strings."""

    def test_login(self):
        self.assertEqual(_match_path("POST", "/auth/login"), "10/minute")

    def test_signup(self):
        self.assertEqual(_match_path("POST", "/auth/signup"), "5/minute")

    def test_google_login(self):
        self.assertEqual(_match_path("GET", "/auth/google/login"), "10/minute")

    def test_google_callback_uses_default(self):
        # Callback must NOT be strictly rate-limited — uses global default
        self.assertEqual(_match_path("GET", "/auth/google/callback"), DEFAULT_LIMIT)

    def test_github_metadata_post(self):
        self.assertEqual(_match_path("POST", "/repositories/github/metadata"), "30/minute")

    def test_github_metadata_get(self):
        self.assertEqual(_match_path("GET", "/repositories/github/metadata"), "30/minute")

    def test_github_connect(self):
        self.assertEqual(_match_path("POST", "/repositories/github"), "10/minute")

    def test_scan(self):
        self.assertEqual(_match_path("POST", "/repositories/42/scan"), "5/hour")

    def test_ask(self):
        self.assertEqual(_match_path("POST", "/repositories/1/ask"), "20/hour")

    def test_explain(self):
        self.assertEqual(_match_path("POST", "/repositories/1/findings/5/explain"), "20/hour")

    def test_fix(self):
        self.assertEqual(_match_path("POST", "/repositories/1/findings/5/fix"), "10/hour")

    def test_test_gen(self):
        self.assertEqual(_match_path("POST", "/repositories/1/findings/5/test"), "10/hour")

    def test_ai_pr_review(self):
        self.assertEqual(_match_path("POST", "/repositories/1/pull-requests/3/ai-review"), "5/hour")

    def test_health_elevated(self):
        self.assertEqual(_match_path("GET", "/health"), "120/minute")

    def test_db_health(self):
        self.assertEqual(_match_path("GET", "/db/health"), "30/minute")

    def test_unmatched_uses_default(self):
        self.assertEqual(_match_path("GET", "/repositories"), DEFAULT_LIMIT)
        self.assertEqual(_match_path("GET", "/auth/me"), DEFAULT_LIMIT)


# ---------------------------------------------------------------------------
# Integration tests — actual HTTP responses
# ---------------------------------------------------------------------------

class TestRateLimitIsolation(RateLimitTestBase):
    """Verify reset_limiter() prevents counter leakage between tests."""

    def test_isolation_first(self):
        responses = _hit(self.client, "GET", "/health", 5)
        for r in responses:
            self.assertEqual(r.status_code, 200)

    def test_isolation_second(self):
        # Counters from test_isolation_first must be gone (reset by setUp).
        responses = _hit(self.client, "GET", "/health", 5)
        for r in responses:
            self.assertEqual(r.status_code, 200)


class TestHealthEndpoint(RateLimitTestBase):
    """Health endpoint must never be blocked under normal probe frequency."""

    def test_health_not_blocked(self):
        # 120/minute limit; fire 30 to confirm they all pass
        responses = _hit(self.client, "GET", "/health", 30)
        for r in responses:
            self.assertEqual(r.status_code, 200)


class Test429Format(RateLimitTestBase):
    """429 response must be safe and provider-neutral."""

    def _exhaust_login(self):
        """Exhaust the login limit (10/min) then return 11th response."""
        for _ in range(10):
            self.client.post("/auth/login", json={"email": "x@x.com", "password": "x"})
        return self.client.post("/auth/login", json={"email": "x@x.com", "password": "x"})

    def test_429_status_code(self):
        resp = self._exhaust_login()
        self.assertEqual(resp.status_code, 429)

    def test_429_body_has_detail(self):
        resp = self._exhaust_login()
        body = resp.json()
        self.assertIn("detail", body)

    def test_429_body_has_code(self):
        resp = self._exhaust_login()
        body = resp.json()
        self.assertEqual(body["code"], "RATE_LIMIT_EXCEEDED")

    def test_429_message_is_safe(self):
        resp = self._exhaust_login()
        body_str = resp.text.lower()
        # Must not leak internal implementation details
        for forbidden in ("redis", "memor", "traceback", "exception",
                          "stack"):
            self.assertNotIn(forbidden, body_str,
                             f"Forbidden word '{forbidden}' found in 429 body")

    def test_429_retry_after_header(self):
        resp = self._exhaust_login()
        self.assertIn("retry-after", resp.headers)
        retry = int(resp.headers["retry-after"])
        self.assertGreater(retry, 0)


class TestLoginRateLimit(RateLimitTestBase):
    """POST /auth/login: 10/minute.
    Requests 1-10 allowed; request 11 blocked.
    """

    def _post_login(self):
        return self.client.post(
            "/auth/login",
            json={"email": "test@example.com", "password": "wrongpassword"},
        )

    def test_below_limit_not_429(self):
        # First 10 may return 401 (wrong password) but NOT 429
        responses = [self._post_login() for _ in range(10)]
        for r in responses:
            self.assertNotEqual(r.status_code, 429)

    def test_above_limit_is_429(self):
        for _ in range(10):
            self._post_login()
        resp = self._post_login()  # 11th — over limit
        self.assertEqual(resp.status_code, 429)


class TestSignupRateLimit(RateLimitTestBase):
    """POST /auth/signup: 5/minute.
    Requests 1-5 allowed; request 6 blocked.
    """

    def _post_signup(self, suffix=""):
        return self.client.post(
            "/auth/signup",
            json={"email": f"rl_signup{suffix}@ratelimit.test",
                  "password": "Password123!"},
        )

    def test_below_limit_not_429(self):
        responses = [self._post_signup(f"b{i}") for i in range(5)]
        for r in responses:
            self.assertNotEqual(r.status_code, 429,
                                f"Got 429 on request: status={r.status_code}")

    def test_above_limit_is_429(self):
        # 5 allowed, 6th blocked
        for i in range(5):
            self._post_signup(f"a{i}")
        resp = self._post_signup("aover")
        self.assertEqual(resp.status_code, 429)


class TestGoogleLoginRateLimit(RateLimitTestBase):
    """GET /auth/google/login: 10/minute.
    Requests 1-10 allowed; request 11 blocked.
    """

    def _get_google_login(self):
        return self.client.get("/auth/google/login", follow_redirects=False)

    def test_below_limit_not_429(self):
        responses = [self._get_google_login() for _ in range(10)]
        for r in responses:
            self.assertNotEqual(r.status_code, 429)

    def test_above_limit_is_429(self):
        for _ in range(10):
            self._get_google_login()
        resp = self._get_google_login()  # 11th
        self.assertEqual(resp.status_code, 429)


class TestGitHubMetadataRateLimit(RateLimitTestBase):
    """POST /repositories/github/metadata: 30/minute.
    Requests 1-30 allowed; request 31 blocked.
    """

    @patch("app.api.routes.repositories.fetch_github_metadata")
    def test_below_limit_not_429(self, mock_meta):
        mock_meta.return_value = _make_github_metadata()
        responses = [
            self.client.post(
                "/repositories/github/metadata",
                json={"url": "https://github.com/o/n"},
            )
            for _ in range(5)
        ]
        for r in responses:
            self.assertNotEqual(r.status_code, 429)

    def test_above_limit_is_429(self):
        # Don't need a valid response here — just exhaust the counter
        for _ in range(30):
            self.client.post(
                "/repositories/github/metadata",
                json={"url": "https://github.com/o/n"},
            )
        resp = self.client.post(
            "/repositories/github/metadata",
            json={"url": "https://github.com/o/n"},
        )
        self.assertEqual(resp.status_code, 429)


class TestGitHubConnectRateLimit(RateLimitTestBase):
    """POST /repositories/github: 10/minute.
    Requests 1-10 allowed; request 11 blocked.
    """

    def test_above_limit_is_429(self):
        # Exhaust the counter (responses may be 4xx — doesn't matter)
        for _ in range(10):
            self.client.post(
                "/repositories/github",
                json={"url": "https://github.com/o/rl-connect"},
            )
        resp = self.client.post(
            "/repositories/github",
            json={"url": "https://github.com/o/rl-connect"},
        )
        self.assertEqual(resp.status_code, 429)


class TestScanRateLimit(RateLimitTestBase):
    """POST /repositories/{id}/scan: 5/hour.
    Requests 1-5 allowed; request 6 blocked.
    """

    def test_above_limit_is_429(self):
        with patch("app.api.routes.repositories.run_background_scan"):
            for _ in range(5):
                self.client.post("/repositories/99999/scan")
            resp = self.client.post("/repositories/99999/scan")
        self.assertEqual(resp.status_code, 429)


class TestAskRateLimit(RateLimitTestBase):
    """POST /repositories/{id}/ask: 20/hour.
    Requests 1-20 allowed; request 21 blocked.
    """

    def test_above_limit_is_429(self):
        for _ in range(20):
            self.client.post("/repositories/99999/ask", json={"question": "test"})
        resp = self.client.post("/repositories/99999/ask", json={"question": "test"})
        self.assertEqual(resp.status_code, 429)


class TestExplainRateLimit(RateLimitTestBase):
    """POST /repositories/{id}/findings/{fid}/explain: 20/hour.
    Requests 1-20 allowed; request 21 blocked.
    """

    def test_above_limit_is_429(self):
        for _ in range(20):
            self.client.post("/repositories/99999/findings/1/explain")
        resp = self.client.post("/repositories/99999/findings/1/explain")
        self.assertEqual(resp.status_code, 429)


class TestFixRateLimit(RateLimitTestBase):
    """POST /repositories/{id}/findings/{fid}/fix: 10/hour.
    Requests 1-10 allowed; request 11 blocked.
    """

    def test_above_limit_is_429(self):
        for _ in range(10):
            self.client.post("/repositories/99999/findings/1/fix")
        resp = self.client.post("/repositories/99999/findings/1/fix")
        self.assertEqual(resp.status_code, 429)


class TestTestGenRateLimit(RateLimitTestBase):
    """POST /repositories/{id}/findings/{fid}/test: 10/hour.
    Requests 1-10 allowed; request 11 blocked.
    """

    def test_above_limit_is_429(self):
        for _ in range(10):
            self.client.post("/repositories/99999/findings/1/test")
        resp = self.client.post("/repositories/99999/findings/1/test")
        self.assertEqual(resp.status_code, 429)


class TestAIPRReviewRateLimit(RateLimitTestBase):
    """POST /repositories/{id}/pull-requests/{pr}/ai-review: 5/hour.
    Requests 1-5 allowed; request 6 blocked.
    """

    def test_above_limit_is_429(self):
        for _ in range(5):
            self.client.post("/repositories/99999/pull-requests/1/ai-review")
        resp = self.client.post("/repositories/99999/pull-requests/1/ai-review")
        self.assertEqual(resp.status_code, 429)


class TestLegitimateRequestsPassThrough(RateLimitTestBase):
    """Existing functionality must not be broken by rate limiting."""

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_auth_me_returns_non_429(self):
        resp = self.client.get("/auth/me")
        self.assertNotEqual(resp.status_code, 429)

    def test_get_repositories_not_blocked(self):
        resp = self.client.get("/repositories")
        self.assertNotEqual(resp.status_code, 429)


if __name__ == "__main__":
    unittest.main()
