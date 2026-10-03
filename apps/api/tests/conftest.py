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
from app.db.database import engine
from app.main import app  # noqa: F401 — re-exported for tests

# A plain object with the attributes the route layer reads from current_user.
FAKE_USER = SimpleNamespace(
    id=1,
    email="test@codelens.test",
    name="Test User",
    password_hash="fakehash",
)


@pytest.fixture(autouse=True, scope="session")
def sync_sequences():
    """Advance PostgreSQL sequences past any rows inserted with explicit IDs.

    Several test classes insert rows with explicit primary keys (e.g. User(id=1))
    to satisfy FK constraints. PostgreSQL SERIAL sequences are NOT advanced by
    explicit-ID inserts. This fixture ensures every sequence starts at MAX(id)+1
    before the test session begins, preventing nextval() collisions with
    pre-seeded rows regardless of test execution order.

    This fixture only affects the test database. It has no production impact.
    """
    with engine.connect() as conn:
        conn.execute(text(
            "SELECT setval('users_id_seq', "
            "COALESCE((SELECT MAX(id) FROM users), 0) + 1, false)"
        ))
        conn.commit()
