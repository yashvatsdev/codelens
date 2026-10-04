"""Shared pytest configuration for CodeLens backend tests.

Provides a lightweight fake-user object whose ``get_current_user`` dependency
override lets integration tests hit protected HTTP endpoints without a real
JWT cookie.

The FakeUser is a plain namespace — NOT a SQLAlchemy ORM instance — which
avoids instrumentation issues when setting attributes outside an ORM session.
Downstream repository-route code only accesses user.id for ownership checks,
so this is safe.

Import pattern for TestClient-based test classes:

    from tests.conftest import FAKE_USER, get_current_user, app

    class MyTest(unittest.TestCase):
        def setUp(self):
            app.dependency_overrides[get_current_user] = lambda: FAKE_USER
            self.client = TestClient(app)

        def tearDown(self):
            app.dependency_overrides.clear()
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.deps import get_current_user  # noqa: F401 — re-exported for tests
from app.core.rate_limit import reset_limiter
from app.db.database import engine
from app.main import app  # noqa: F401 — re-exported for tests

# A plain object with the attributes the route layer reads from current_user.
FAKE_USER = SimpleNamespace(
    id=1,
    email="test@codelens.test",
    name="Test User",
    password_hash="fakehash",
)


@pytest.fixture(autouse=True)
def sync_sequences():
    """Advance PostgreSQL sequences and reset rate-limiter before every test.

    Two responsibilities:

    1. Sequence synchronisation:
       Several test classes insert rows with explicit primary keys (e.g.
       User(id=1)) to satisfy FK constraints.  PostgreSQL SERIAL sequences are
       NOT advanced by explicit-ID inserts.  This fixture re-synchronises the
       sequence so that implicit inserts in later tests receive IDs higher than
       any explicitly inserted ID, preventing primary-key collisions regardless
       of test execution order or whether the database is fresh or reused.

       Example:
         1. test_analyzer.setUp() inserts User(id=1) — sequence stays at 1.
         2. This fixture re-runs before test_ask.
         3. MAX(id)=1, so setval makes the next nextval() return 2.
         4. test_ask.setUp() inserts User(email=...) — gets id=2, no collision.

    2. Rate-limiter reset:
       The application rate limiter uses an in-memory counter shared across
       all requests to the same ``app`` singleton.  Without a reset between
       tests, requests made in earlier tests (e.g. auth tests that POST to
       /auth/signup) can consume the per-minute quota and cause subsequent
       rate-limit tests to see unexpected 429 responses.

       ``reset_limiter()`` replaces the in-memory storage object so all
       counters start at zero before each test.

    Both operations are test-infrastructure only.  Neither has any production
    impact.
    """
    reset_limiter()
    with engine.connect() as conn:
        conn.execute(text(
            "SELECT setval('users_id_seq', "
            "COALESCE((SELECT MAX(id) FROM users), 0) + 1, false)"
        ))
        conn.commit()
