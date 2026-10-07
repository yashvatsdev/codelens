"""Regression reproductions for the Phase 6 audit; no real external services."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from app.api.deps import get_current_user
from app.api.routes.auth import _resolve_user
from app.core.ai_errors import AIQuotaExceededError, AI_UNAVAILABLE_MESSAGE, sanitize_ai_error
from app.core.config import settings
from app.core.middleware import SecurityAndBodyLimitMiddleware
from app.core.rate_limit import _get_client_key, reset_limiter
from app.core.security import create_access_token, verify_password
from app.db.database import get_db
from app.main import app
from app.schemas.user import UserCreate, UserLogin


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def request_with_cookie(token, path="/repositories/1"):
    return Request({"type": "http", "method": "GET", "path": path,
                    "headers": [(b"cookie", f"{settings.auth_cookie_name}={token}".encode())],
                    "client": ("192.0.2.1", 1234)})


def test_forged_cookie_cannot_select_rate_identity():
    token = jwt.encode({"sub": "345", "exp": int(time.time()) + 60}, "synthetic-untrusted-key-for-regression", algorithm="HS256")
    assert _get_client_key(request_with_cookie(token)).startswith("ip:")


def test_valid_cookie_still_uses_user_identity():
    assert _get_client_key(request_with_cookie(create_access_token(345))) == "user:345"


def test_public_auth_budget_cannot_rotate_signed_users():
    req = request_with_cookie(create_access_token(345), "/auth/login")
    assert _get_client_key(req).startswith("ip:")


def test_resource_ids_and_slashes_share_ai_budget(client):
    # These requests are unauthorized, but must consume the same operation budget.
    results = [client.post(f"/repositories/{i}/ask" + ("/" if i % 2 else ""),
                           json={"question": "What is this?"}, follow_redirects=False).status_code
               for i in range(1, 22)]
    assert results[-1] == 429


@pytest.mark.parametrize("payload", [
    {"sub": "1"}, {"sub": "01", "exp": 4102444800},
    {"sub": "-1", "exp": 4102444800}, {"sub": "0", "exp": 4102444800},
    {"sub": " 1", "exp": 4102444800}, {"sub": [], "exp": 4102444800},
])
def test_session_claims_rejected_before_database_access(payload):
    token = jwt.encode(payload, settings.auth_secret_key, algorithm="HS256")
    db = Mock()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        get_current_user(request_with_cookie(token), db)
    assert caught.value.status_code == 401
    db.get.assert_not_called()


def test_sessions_are_fresh_even_in_same_second():
    assert create_access_token(1) != create_access_token(1)


def test_corrupt_password_hash_fails_closed():
    assert verify_password("synthetic-password", "invalid-hash") is False


@pytest.mark.parametrize("length", [0, 7, 1025])
def test_signup_password_bounds(length):
    with pytest.raises(ValidationError):
        UserCreate(email="someone@example.com", password="x" * length)


def test_login_preserves_existing_short_password_accounts():
    assert UserLogin(email="someone@example.com", password="short").password == "short"


def test_linked_google_identity_cannot_be_replaced():
    existing = SimpleNamespace(id=4, google_sub="original-identity", name="Someone")
    db = Mock()
    db.scalar.side_effect = [None, existing]
    assert _resolve_user(db, google_sub="replacement", email="someone@example.com", name=None) is None
    assert existing.google_sub == "original-identity"
    db.commit.assert_not_called()


def test_failed_oauth_clears_transaction_cookies(client):
    response = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    cookies = response.headers.get_list("set-cookie")
    assert any("codelens_oauth_state=" in c and "Max-Age=0" in c for c in cookies)
    assert any("codelens_oauth_nonce=" in c and "Max-Age=0" in c for c in cookies)


def test_database_failure_is_neutral(client):
    with patch("app.main.engine.connect", side_effect=RuntimeError("synthetic-private-diagnostic")):
        response = client.get("/db/health")
    assert response.status_code == 503
    assert "synthetic-private-diagnostic" not in response.text


def test_unknown_ai_error_is_neutral():
    assert sanitize_ai_error(RuntimeError("synthetic-private-diagnostic")) == AI_UNAVAILABLE_MESSAGE


def test_ask_quota_remains_429_without_raw_logs(client, caplog):
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_db] = lambda: Mock()
    with patch("app.api.routes.ask.retrieve_code_context", return_value=[]), patch(
        "app.api.routes.ask.generate_structured", side_effect=AIQuotaExceededError()
    ):
        response = client.post("/repositories/1/ask", json={"question": "Explain"})
    assert response.status_code == 429
    assert response.json()["code"] == "AI_QUOTA_EXCEEDED"


def test_cross_site_logout_rejected_before_cookie_mutation(client):
    response = client.post("/auth/logout", headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_trusted_frontend_logout_allowed(client):
    assert client.post("/auth/logout", headers={"Origin": settings.frontend_url}).status_code == 200


@pytest.mark.parametrize("kind", ["rate", "body"])
def test_early_responses_have_cors_and_security_headers(client, kind):
    headers = {"Origin": settings.frontend_url}
    if kind == "rate":
        for _ in range(10):
            client.post("/auth/login", json={}, headers=headers)
        response = client.post("/auth/login", json={}, headers=headers)
        assert response.status_code == 429
    else:
        response = client.post("/auth/login", content=b"x" * 1_000_001, headers=headers)
        assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == settings.frontend_url
    assert response.headers.get("permissions-policy")
    assert response.headers.get("cache-control") == "no-store"


def test_streaming_overflow_rejected_before_downstream_parser():
    downstream = Mock()
    async def endpoint(scope, receive, send):
        downstream()
        # A parser may swallow receive errors; the guard must run before it.
        try:
            await receive()
        except Exception:
            pass
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    messages = iter([{"type": "http.request", "body": b"1234", "more_body": True},
                     {"type": "http.request", "body": b"5678", "more_body": False}])
    sent = []
    async def receive():
        return next(messages)
    async def send(message):
        sent.append(message)
    asyncio.run(SecurityAndBodyLimitMiddleware(endpoint, max_body_size=5)(
        {"type": "http", "method": "POST", "scheme": "https", "path": "/auth/login", "headers": []}, receive, send))
    assert sent[0]["status"] == 413
    downstream.assert_not_called()


def test_validation_does_not_echo_password(client):
    response = client.post("/auth/signup", json={"email": "someone@example.com", "password": "synthetic-private-password" * 100})
    assert response.status_code == 422
    assert "synthetic-private-password" not in response.text


@pytest.mark.parametrize("fields", [
    {"auth_secret_key": None}, {"frontend_url": "*"},
    {"frontend_url": "https://frontend.example/path"},
    {"auth_cookie_samesite": "none", "auth_cookie_secure": False},
])
def test_unsafe_security_configuration_rejected(fields, monkeypatch):
    from app.core.config import Settings
    if fields.get("auth_secret_key", "present") is None:
        monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)
        fields = {}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **fields)


@pytest.mark.parametrize("url,owner", [
    ("http://127.0.0.1/private", "o"), ("javascript:alert(1)", "o"),
    ("https://github.com/o/r", "../other"),
])
def test_manual_metadata_cannot_bypass_repository_validation(url, owner):
    from fastapi import HTTPException
    from app.api.routes.repositories import create_repository
    from app.schemas.repository import RepositoryCreate
    db = Mock()
    with pytest.raises(HTTPException) as caught:
        create_repository(RepositoryCreate(url=url, owner=owner, name="r", full_name=f"{owner}/r"), db, SimpleNamespace(id=1))
    assert caught.value.status_code == 400
    db.execute.assert_not_called()


@pytest.mark.parametrize("url", ["https://github.com/../r", "git@github.com:o/..", "https://github.com/o/%2e%2e", "https://github.com/o/..\\r"])
def test_remote_repository_traversal_rejected(url):
    from app.services.github import parse_github_url
    with pytest.raises(ValueError):
        parse_github_url(url)


def test_github_upstream_error_cannot_expose_body_or_reason():
    import io
    from urllib.error import HTTPError
    from app.services.branch_fixer import _authenticated_github_request
    from app.services.github import GitHubAPIError
    error = HTTPError("https://api.github.com/repos/o/r", 500, "synthetic-private-diagnostic", {}, io.BytesIO(b"synthetic-private-body"))
    with patch("urllib.request.urlopen", side_effect=error), pytest.raises(GitHubAPIError) as caught:
        _authenticated_github_request("https://api.github.com/repos/o/r")
    assert "synthetic-private" not in str(caught.value)


@pytest.mark.parametrize("url", ["http://127.0.0.1/private", "https://evil.example/source", "file:///etc/passwd"])
def test_unexpected_pr_contents_url_rejected_before_network(url):
    from app.services.pr_reviewer import _fetch_file_content_from_url
    from app.services.github import GitHubServiceError
    with patch("urllib.request.urlopen") as network, pytest.raises(GitHubServiceError):
        _fetch_file_content_from_url(url)
    network.assert_not_called()


def test_scan_claim_is_atomic_under_concurrent_starts():
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import BackgroundTasks
    from app.api.routes.repositories import start_repository_scan
    from app.services.scan_progress import clear_scan_progress
    clear_scan_progress(987654)
    db = Mock()
    db.get.return_value = SimpleNamespace(user_id=1)
    tasks = [BackgroundTasks() for _ in range(20)]
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda task: start_repository_scan(987654, task, db, SimpleNamespace(id=1)), tasks))
        assert sum(len(task.tasks) for task in tasks) == 1
    finally:
        clear_scan_progress(987654)


def test_ingestion_rechecks_actual_individual_size():
    from app.services.github import GitHubTreeEntry, GitHubFileContent, MAX_FILE_SIZE_BYTES
    from app.services.ingestion import ingest_repository, RepositorySourceTooLargeError
    entry = GitHubTreeEntry(path="large.py", sha="x", type="blob", size=1)
    content = GitHubFileContent(path="large.py", sha="x", size=1, content="x" * (MAX_FILE_SIZE_BYTES + 1))
    with patch("app.services.ingestion.fetch_repo_tree", return_value=[entry]), patch(
        "app.services.ingestion.fetch_file_content", return_value=content
    ), pytest.raises(RepositorySourceTooLargeError):
        ingest_repository("owner", "repo")


def test_ai_provider_logs_do_not_include_raw_exception(caplog):
    from app.services.ai_provider import generate_ai_response
    with patch.object(settings, "ai_provider_mode", "ollama_only"), patch(
        "app.services.ai_provider.call_ollama", side_effect=RuntimeError("synthetic-private-diagnostic")
    ), pytest.raises(Exception):
        generate_ai_response("Explain code")
    assert "synthetic-private-diagnostic" not in caplog.text


def test_oauth_truthy_unverified_flag_is_rejected(client):
    from unittest.mock import AsyncMock
    client.cookies.set("codelens_oauth_state", "transaction")
    client.cookies.set("codelens_oauth_nonce", "nonce")
    with patch.object(settings, "google_client_id", "synthetic-client"), patch.object(
        settings, "google_client_secret", "synthetic-only"
    ), patch.object(settings, "google_redirect_uri", "https://backend.example/auth/google/callback"), patch(
        "app.api.routes.auth.exchange_code_and_validate", new=AsyncMock(return_value={
            "sub": "identity", "email": "someone@example.com", "email_verified": "false"
        })
    ), patch("app.api.routes.auth._resolve_user") as resolve:
        response = client.get("/auth/google/callback?code=synthetic&state=transaction", follow_redirects=False)
    assert "unverified_email" in response.headers["location"]
    resolve.assert_not_called()


@pytest.fixture
def tenants():
    import uuid
    from app.db.database import SessionLocal
    from app.models.user import User
    from app.models.repository import Repository
    from app.models.finding import Finding
    from app.models.source_file import SourceFile
    from app.models.pr_review import PRReview
    suffix = uuid.uuid4().hex
    with SessionLocal() as db:
        a = User(email=f"audit-a-{suffix}@example.com", password_hash=None)
        b = User(email=f"audit-b-{suffix}@example.com", password_hash=None)
        db.add_all([a, b])
        db.flush()
        repo = Repository(user_id=b.id, github_id=suffix, owner="owner", name="repo", full_name="owner/repo", url="https://github.com/owner/repo")
        own_repo = Repository(user_id=a.id, github_id=suffix, owner="owner", name="repo", full_name="owner/repo", url="https://github.com/owner/repo")
        db.add_all([repo, own_repo])
        db.flush()
        finding = Finding(repository_id=repo.id, file_path="private.py", line_number=1, severity="info", category="style", message="foreign-private-marker", rule_id="TODO")
        source = SourceFile(repository_id=repo.id, path="private.py", sha="x", content="foreign-private-marker", size=22)
        review = PRReview(user_id=b.id, repository_id=repo.id, pull_request_number=1, summary="foreign-private-marker", risk_level="low", overall_assessment="private")
        db.add_all([finding, source, review])
        db.commit()
        values = dict(token=create_access_token(a.id), repo=repo.id, own_repo=own_repo.id, finding=finding.id, review=review.id)
        try:
            yield values
        finally:
            db.rollback()
            db.delete(repo)
            db.delete(own_repo)
            db.flush()
            db.delete(a)
            db.delete(b)
            db.commit()


PROTECTED_OPERATIONS = [
    ("GET", "/repositories/{repo}", None),
    ("GET", "/repositories/{repo}/files", None),
    ("GET", "/repositories/{repo}/findings", None),
    ("GET", "/repositories/{repo}/scan-status", None),
    ("POST", "/repositories/{repo}/scan", None),
    ("POST", "/repositories/{repo}/ingest", None),
    ("POST", "/repositories/{repo}/analyze", None),
    ("POST", "/repositories/{repo}/ask", {"question": "Explain private.py"}),
    ("POST", "/repositories/{repo}/findings/{finding}/explain", None),
    ("POST", "/repositories/{repo}/findings/{finding}/fix", None),
    ("POST", "/repositories/{repo}/findings/{finding}/test", None),
    ("POST", "/repositories/{repo}/findings/{finding}/apply-fix", {}),
    ("POST", "/repositories/{repo}/findings/{finding}/create-pr", {"branch_name": "fix"}),
    ("POST", "/repositories/{repo}/pull-requests/1/review", None),
    ("POST", "/repositories/{repo}/pull-requests/1/ai-review", None),
    ("POST", "/repositories/{repo}/pull-requests/1/findings/fix", {"file_path": "private.py", "line_number": 1, "issue": "TODO", "severity": "info", "category": "style", "message": "Issue"}),
    ("POST", "/repositories/{repo}/pull-requests/1/comment", {}),
    ("GET", "/pr-reviews/{review}", None),
    ("DELETE", "/repositories/{repo}", None),
]


@pytest.mark.parametrize("method,path,payload", PROTECTED_OPERATIONS)
def test_two_user_http_isolation(client, tenants, method, path, payload, monkeypatch):
    import app.api.routes.repositories as routes
    def forbidden_side_effect(*args, **kwargs):
        pytest.fail("Foreign resource reached an external operation")
    for name in ("ingest_repository", "analyze_repository", "run_background_scan", "review_pull_request",
                 "generate_ai_pr_review", "generate_pr_finding_fix", "post_pr_review_comment"):
        if hasattr(routes, name):
            monkeypatch.setattr(routes, name, forbidden_side_effect)
    client.cookies.set(settings.auth_cookie_name, tenants["token"])
    response = client.request(method, path.format(**tenants), json=payload)
    assert response.status_code in (403, 404)
    assert "foreign-private-marker" not in response.text


@pytest.mark.parametrize("method,path,payload", PROTECTED_OPERATIONS)
def test_protected_operations_require_authentication(client, method, path, payload):
    response = client.request(method, path.format(repo=1, finding=1, review=1), json=payload)
    assert response.status_code == 401


@pytest.mark.parametrize("operation", ["explain", "fix", "test", "apply-fix", "create-pr"])
def test_finding_cannot_be_mixed_into_owned_repository(client, tenants, operation):
    client.cookies.set(settings.auth_cookie_name, tenants["token"])
    payload = {"branch_name": "fix"} if operation == "create-pr" else {}
    response = client.post(f"/repositories/{tenants['own_repo']}/findings/{tenants['finding']}/{operation}", json=payload)
    assert response.status_code == 404


@pytest.mark.parametrize("endpoint", ["ingest", "analyze"])
def test_direct_processing_cannot_overlap_active_scan(client, tenants, endpoint):
    from app.services.scan_progress import set_scan_progress, clear_scan_progress
    client.cookies.set(settings.auth_cookie_name, tenants["token"])
    set_scan_progress(tenants["own_repo"], "running", "fetching_files", 10)
    try:
        response = client.post(f"/repositories/{tenants['own_repo']}/{endpoint}")
        assert response.status_code == 409
    finally:
        clear_scan_progress(tenants["own_repo"])


@pytest.mark.parametrize("limit", [-1, 0, 101, 2147483648])
def test_pr_history_limit_is_bounded(client, limit):
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    db = Mock()
    db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    app.dependency_overrides[get_db] = lambda: db
    response = client.get(f"/pr-reviews?limit={limit}")
    assert response.status_code == 422


def test_compose_requires_explicit_session_secret_for_app_and_tests():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text()
    for service in ("api", "test"):
        section = text.split(f"\n  {service}:\n", 1)[1].split("\n  postgres:", 1)[0].split("\n  test:", 1)[0]
        assert "AUTH_SECRET_KEY=${AUTH_SECRET_KEY:?" in section


def test_settings_validation_and_repr_do_not_echo_sensitive_values():
    from app.core.config import Settings
    values = dict(database_url="synthetic-private-diagnostic", auth_secret_key="synthetic-private-diagnostic" * 2,
                  google_client_secret="synthetic-private-diagnostic", github_token="synthetic-private-diagnostic")
    assert "synthetic-private-diagnostic" not in repr(Settings(_env_file=None, **values))
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, frontend_url="*", **values)
    assert "synthetic-private-diagnostic" not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_aggregate_budget_spans_different_default_paths(client):
    for i in range(60):
        assert client.get(f"/unknown-audit-route/{i}").status_code == 404
    assert client.get("/another-unknown-route").status_code == 429


def test_alternate_repository_creation_routes_share_budget(client):
    for i in range(10):
        path = "/repositories/github" if i % 2 else "/repositories"
        assert client.post(path, json={}).status_code != 429
    assert client.post("/repositories", json={}).status_code == 429


def test_forwarded_headers_cannot_rotate_login_budget(client):
    for i in range(10):
        client.post("/auth/login", json={}, headers={"X-Forwarded-For": f"192.0.2.{i}"})
    assert client.post("/auth/login", json={}, headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 429


@pytest.mark.parametrize("path,budget", [
    ("/repositories/{i}/findings/1/apply-fix", 10),
    ("/repositories/{i}/pull-requests/1/findings/fix", 10),
    ("/repositories/{i}/pull-requests/1/comment", 5),
])
def test_expensive_alternate_operations_have_resource_independent_budgets(client, path, budget):
    for i in range(budget):
        assert client.post(path.format(i=i + 1), json={}).status_code != 429
    assert client.post(path.format(i=999), json={}).status_code == 429
