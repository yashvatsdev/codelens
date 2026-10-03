"""
Tests for Google OAuth endpoints and user resolution logic.

All tests mock Google network calls — no real Google credentials needed.
"""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db.database import SessionLocal
from app.models.user import User
from app.core.security import get_password_hash, create_access_token
from app.api.routes.auth import _resolve_user, _constant_time_compare


# ---------------------------------------------------------------------------
# Fake Google claims returned after successful ID token validation
# ---------------------------------------------------------------------------
def _fake_claims(
    sub="google-sub-test-123",
    email="google_user@example.com",
    email_verified=True,
    name="Google Test User",
):
    return {
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "name": name,
        "iss": "https://accounts.google.com",
        "aud": "fake-client-id",
        "exp": int(time.time()) + 3600,
        "nonce": "test-nonce",
    }


def _make_exchange_mock(claims: dict):
    """Return an AsyncMock that simulates a successful token exchange."""
    return AsyncMock(return_value=claims)


def _make_auth_url_mock(url: str = "https://accounts.google.com/o/oauth2/auth?fake=1"):
    return AsyncMock(return_value=url)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------
class TestGoogleOAuth(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        for email in [
            "google_user@example.com",
            "existing_pw@example.com",
            "new_google@example.com",
            "conflict_a@example.com",
            "conflict_b@example.com",
            "google_existing@example.com",
        ]:
            self.db.query(User).filter_by(email=email).delete()
        self.db.commit()

    # -----------------------------------------------------------------------
    # 1. Google login endpoint exists
    # -----------------------------------------------------------------------
    def test_google_login_endpoint_exists_when_unconfigured(self):
        """Even unconfigured, the /auth/google/login route is registered (returns 503)."""
        response = self.client.get("/auth/google/login")
        self.assertIn(response.status_code, (302, 503))

    # -----------------------------------------------------------------------
    # 2. Google login redirects to Google when configured
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.build_authorization_url", new_callable=AsyncMock)
    def test_google_login_redirects_when_configured(self, mock_build, mock_settings):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "fake-client-id"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_build.return_value = "https://accounts.google.com/o/oauth2/auth?fake=1"

        response = self.client.get("/auth/google/login")
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response.headers["location"])
        mock_build.assert_awaited_once()

    # -----------------------------------------------------------------------
    # 3. Google login fails cleanly when unconfigured
    # -----------------------------------------------------------------------
    def test_google_login_returns_503_when_not_configured(self):
        """When GOOGLE_CLIENT_ID etc. are absent, endpoint returns 503 with safe message."""
        with patch("app.api.routes.auth.settings") as mock_settings:
            mock_settings.google_oauth_configured = False
            response = self.client.get("/auth/google/login")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertIn("not available", body["detail"])
        # Must NOT expose secrets
        self.assertNotIn("secret", str(body).lower())
        self.assertNotIn("client_id", str(body).lower())

    # -----------------------------------------------------------------------
    # 4 & 5. Callback creates a new Google user (CASE C)
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_callback_creates_new_google_user(self, mock_exchange, mock_settings):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "fake-client-id"
        mock_settings.google_client_secret = "fake-secret"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(
            sub="new-sub-999",
            email="new_google@example.com",
        )

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "auth-code", "state": "correct-state"},
            cookies={
                "codelens_oauth_state": "correct-state",
                "codelens_oauth_nonce": "test-nonce",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["location"])

        # Verify user was created
        user = self.db.scalar(select(User).where(User.email == "new_google@example.com"))
        self.assertIsNotNone(user)

    # -----------------------------------------------------------------------
    # 6. New Google user has correct fields
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_new_google_user_has_null_password_and_correct_sub(
        self, mock_exchange, mock_settings
    ):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "fake-client-id"
        mock_settings.google_client_secret = "fake-secret"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(
            sub="sub-null-pw",
            email="google_user@example.com",
        )

        self.client.get(
            "/auth/google/callback",
            params={"code": "code", "state": "st"},
            cookies={"codelens_oauth_state": "st", "codelens_oauth_nonce": "test-nonce"},
        )

        user = self.db.scalar(select(User).where(User.email == "google_user@example.com"))
        self.assertIsNotNone(user)
        self.assertIsNone(user.password_hash)
        self.assertEqual(user.google_sub, "sub-null-pw")

    # -----------------------------------------------------------------------
    # 7. Existing email/password user is linked when verified email matches (CASE B)
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_existing_password_user_linked_by_email(self, mock_exchange, mock_settings):
        # Create an existing password user
        existing = User(
            email="existing_pw@example.com",
            password_hash=get_password_hash("mypassword"),
            google_sub=None,
        )
        self.db.add(existing)
        self.db.commit()
        self.db.refresh(existing)
        original_id = existing.id

        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "fake-client-id"
        mock_settings.google_client_secret = "fake-secret"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(
            sub="newly-linked-sub",
            email="existing_pw@example.com",
        )

        self.client.get(
            "/auth/google/callback",
            params={"code": "code", "state": "st"},
            cookies={"codelens_oauth_state": "st", "codelens_oauth_nonce": "test-nonce"},
        )

        self.db.expire_all()
        user = self.db.get(User, original_id)
        # Same user — not duplicated
        self.assertEqual(user.id, original_id)
        # google_sub was linked
        self.assertEqual(user.google_sub, "newly-linked-sub")
        # Password hash preserved
        self.assertIsNotNone(user.password_hash)

    # -----------------------------------------------------------------------
    # 8. Existing user is NOT duplicated
    # -----------------------------------------------------------------------
    def test_no_duplicate_user_created(self):
        existing = User(email="existing_pw@example.com", password_hash="hash")
        self.db.add(existing)
        self.db.commit()
        count_before = self.db.query(User).count()

        with patch("app.api.routes.auth.settings") as ms, \
             patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock) as me:
            ms.google_oauth_configured = True
            ms.google_client_id = "cid"
            ms.google_client_secret = "cs"
            ms.google_redirect_uri = "http://localhost:8000/auth/google/callback"
            ms.frontend_url = "http://localhost:3000"
            ms.auth_cookie_name = "codelens_session"
            ms.auth_cookie_secure = False
            ms.auth_cookie_samesite = "lax"
            ms.auth_token_expire_minutes = 10080
            me.return_value = _fake_claims(sub="sub-x", email="existing_pw@example.com")
            self.client.get(
                "/auth/google/callback",
                params={"code": "c", "state": "s"},
                cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
            )

        self.db.expire_all()
        count_after = self.db.query(User).count()
        self.assertEqual(count_before, count_after)

    # -----------------------------------------------------------------------
    # 9. Existing Google user authenticates to the same CodeLens account (CASE A)
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_existing_google_user_authenticates_to_same_account(
        self, mock_exchange, mock_settings
    ):
        existing = User(
            email="google_existing@example.com",
            password_hash=None,
            google_sub="google-sub-test-123",
        )
        self.db.add(existing)
        self.db.commit()
        self.db.refresh(existing)
        original_id = existing.id

        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(
            sub="google-sub-test-123",
            email="google_existing@example.com",
        )

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["location"])
        self.db.expire_all()
        self.assertEqual(self.db.query(User).filter_by(google_sub="google-sub-test-123").count(), 1)
        user = self.db.get(User, original_id)
        self.assertEqual(user.google_sub, "google-sub-test-123")

    # -----------------------------------------------------------------------
    # 10 & 11. Identity conflict rejected safely (google_sub → user A, email → user B)
    # -----------------------------------------------------------------------
    def test_identity_conflict_rejected(self):
        user_a = User(email="conflict_a@example.com", password_hash=None, google_sub="sub-a")
        user_b = User(email="conflict_b@example.com", password_hash="hash")
        self.db.add_all([user_a, user_b])
        self.db.commit()

        # google_sub belongs to user_a, email claims to be user_b
        result = _resolve_user(
            self.db,
            google_sub="sub-a",
            email="conflict_b@example.com",
            name=None,
        )
        self.assertIsNone(result)

    # -----------------------------------------------------------------------
    # 12. Missing google_sub is rejected
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_missing_google_sub_redirects_to_login_error(self, mock_exchange, mock_settings):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        claims = _fake_claims()
        claims.pop("sub")
        mock_exchange.return_value = claims

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=missing_sub", response.headers["location"])

    # -----------------------------------------------------------------------
    # 13. Missing email is rejected
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_missing_email_rejected(self, mock_exchange, mock_settings):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        claims = _fake_claims()
        claims.pop("email")
        mock_exchange.return_value = claims

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=unverified_email", response.headers["location"])

    # -----------------------------------------------------------------------
    # 14. Unverified email is rejected
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_unverified_email_rejected(self, mock_exchange, mock_settings):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(email_verified=False)

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=unverified_email", response.headers["location"])

    # -----------------------------------------------------------------------
    # 15. Invalid (missing) state cookie is rejected
    # -----------------------------------------------------------------------
    def test_invalid_state_rejected(self):
        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "wrong-state"},
            cookies={
                "codelens_oauth_state": "correct-state",
                "codelens_oauth_nonce": "nonce",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=invalid_state", response.headers["location"])

    # -----------------------------------------------------------------------
    # 16. Invalid / failed ID token is rejected
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_invalid_id_token_rejected(self, mock_exchange, mock_settings):
        from app.core.oauth import GoogleAuthError
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_exchange.side_effect = GoogleAuthError("token invalid")

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "n"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=token_error", response.headers["location"])

    # -----------------------------------------------------------------------
    # 17. OAuth denial (error param from Google) is handled
    # -----------------------------------------------------------------------
    def test_oauth_denial_handled(self):
        response = self.client.get(
            "/auth/google/callback",
            params={"error": "access_denied"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "n"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_denied", response.headers["location"])

    # -----------------------------------------------------------------------
    # 18. Successful Google auth creates the codelens_session cookie
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_successful_callback_sets_session_cookie(self, mock_exchange, mock_settings):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(
            sub="sub-cookie-check",
            email="google_user@example.com",
        )

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("codelens_session", response.cookies)

    # -----------------------------------------------------------------------
    # 19. JWT subject is the CodeLens user ID
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_jwt_subject_is_codelens_user_id(self, mock_exchange, mock_settings):
        import jwt as pyjwt
        from app.core.config import settings as real_settings

        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(
            sub="sub-jwt-check",
            email="google_user@example.com",
        )

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
        )

        session_cookie = response.cookies.get("codelens_session")
        self.assertIsNotNone(session_cookie)

        payload = pyjwt.decode(
            session_cookie,
            real_settings.auth_secret_key,
            algorithms=["HS256"],
        )
        user_id = int(payload["sub"])

        self.db.expire_all()
        user = self.db.get(User, user_id)
        self.assertIsNotNone(user)
        self.assertEqual(user.google_sub, "sub-jwt-check")

    # -----------------------------------------------------------------------
    # 20. Successful callback redirects to /dashboard
    # -----------------------------------------------------------------------
    @patch("app.api.routes.auth.settings")
    @patch("app.api.routes.auth.exchange_code_and_validate", new_callable=AsyncMock)
    def test_callback_redirects_to_dashboard(self, mock_exchange, mock_settings):
        mock_settings.google_oauth_configured = True
        mock_settings.google_client_id = "cid"
        mock_settings.google_client_secret = "cs"
        mock_settings.google_redirect_uri = "http://localhost:8000/auth/google/callback"
        mock_settings.frontend_url = "http://localhost:3000"
        mock_settings.auth_cookie_name = "codelens_session"
        mock_settings.auth_cookie_secure = False
        mock_settings.auth_cookie_samesite = "lax"
        mock_settings.auth_token_expire_minutes = 10080
        mock_exchange.return_value = _fake_claims(
            sub="sub-dashboard-check",
            email="google_user@example.com",
        )

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            cookies={"codelens_oauth_state": "s", "codelens_oauth_nonce": "test-nonce"},
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers["location"]
        self.assertTrue(location.endswith("/dashboard"), msg=f"Expected /dashboard, got: {location}")

    # -----------------------------------------------------------------------
    # 21. Password login still works for normal users
    # -----------------------------------------------------------------------
    def test_password_login_still_works(self):
        user = User(
            email="existing_pw@example.com",
            password_hash=get_password_hash("correct_pass"),
        )
        self.db.add(user)
        self.db.commit()

        response = self.client.post(
            "/auth/login",
            json={"email": "existing_pw@example.com", "password": "correct_pass"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "existing_pw@example.com")

    # -----------------------------------------------------------------------
    # 22. Password login for Google-only user returns 401 cleanly
    # -----------------------------------------------------------------------
    def test_password_login_google_only_user_returns_401(self):
        user = User(
            email="google_user@example.com",
            password_hash=None,
            google_sub="sub-google-only",
        )
        self.db.add(user)
        self.db.commit()

        response = self.client.post(
            "/auth/login",
            json={"email": "google_user@example.com", "password": "anything"},
        )
        self.assertEqual(response.status_code, 401)
        # No 500 / AttributeError / stack trace exposed
        body = response.json()
        self.assertNotIn("traceback", str(body).lower())
        self.assertNotIn("attributeerror", str(body).lower())

    # -----------------------------------------------------------------------
    # 23. Repository ownership remains tied to same user
    # -----------------------------------------------------------------------
    def test_resolve_user_case_a_preserves_identity(self):
        """Case A: same google_sub always returns same user object."""
        user = User(
            email="google_existing@example.com",
            password_hash=None,
            google_sub="persistent-sub",
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        original_id = user.id

        resolved = _resolve_user(
            self.db,
            google_sub="persistent-sub",
            email="google_existing@example.com",
            name=None,
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, original_id)

    # -----------------------------------------------------------------------
    # Utility: constant-time compare
    # -----------------------------------------------------------------------
    def test_constant_time_compare(self):
        self.assertTrue(_constant_time_compare("abc", "abc"))
        self.assertFalse(_constant_time_compare("abc", "xyz"))
        self.assertFalse(_constant_time_compare("abc", "abcd"))

    # -----------------------------------------------------------------------
    # Missing state cookie → redirects with error
    # -----------------------------------------------------------------------
    def test_missing_state_cookie_redirects_to_login_error(self):
        response = self.client.get(
            "/auth/google/callback",
            params={"code": "c", "state": "s"},
            # no cookies at all
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=missing_state", response.headers["location"])

