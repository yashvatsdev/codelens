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

from app.api.deps import get_current_user  # noqa: F401 — re-exported for tests
from app.main import app  # noqa: F401 — re-exported for tests

# A plain object with the attributes the route layer reads from current_user.
FAKE_USER = SimpleNamespace(
    id=1,
    email="test@codelens.test",
    name="Test User",
    password_hash="fakehash",
)
