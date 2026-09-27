import unittest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import SessionLocal
from app.api.deps import get_current_user
from tests.conftest import FAKE_USER

from app.models.repository import Repository
from app.models.finding import Finding
from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.user import User


class TestDashboardFindingsTrend(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        
        # Override dependency for auth
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        self.client = TestClient(app)
        
        # Find or create users safely without hitting sequence desync
        self.user1 = self.db.query(User).filter_by(id=FAKE_USER.id).first()
        if not self.user1:
            self.user1 = User(id=FAKE_USER.id, email="u1@test.com", password_hash="x", name="u1")
            self.db.add(self.user1)
            self.db.commit()
            
        self.user2 = self.db.query(User).filter(User.id != FAKE_USER.id).first()
        if not self.user2:
            self.user2 = User(id=999, email="u999@test.com", password_hash="y", name="u999")
            self.db.add(self.user2)
            self.db.commit()
            
        # Clean up any left over test repos
        self.db.query(Repository).filter(Repository.github_id.in_(["repo1_gh_trend", "repo2_gh_trend", "repo3_gh_trend"])).delete(synchronize_session=False)
        self.db.commit()

        # Seed test repositories
        self.repo1 = Repository(
            github_id="repo1_gh_trend",
            name="repo1",
            full_name="owner/repo1_trend",
            owner="owner",
            url="https://github.com/owner/repo1_trend",
            user_id=self.user1.id,
        )
        self.repo2 = Repository(
            github_id="repo2_gh_trend",
            name="repo2",
            full_name="owner/repo2_trend",
            owner="owner",
            url="https://github.com/owner/repo2_trend",
            user_id=self.user1.id,
        )
        self.repo3 = Repository(
            github_id="repo3_gh_trend",
            name="repo3",
            full_name="other/repo3_trend",
            owner="other",
            url="https://github.com/other/repo3_trend",
            user_id=self.user2.id,
        )
        self.db.add_all([self.repo1, self.repo2, self.repo3])
        self.db.commit()

    def tearDown(self):
        # Clean up only the objects created by this test
        self.db.delete(self.repo1)
        self.db.delete(self.repo2)
        self.db.delete(self.repo3)
        self.db.commit()
        self.db.close()
        app.dependency_overrides.clear()

    def test_get_findings_trend_empty(self):
        response = self.client.get("/dashboard/findings-trend")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_findings_trend_with_snapshots(self):
        now = datetime.utcnow()
        snap1 = AnalysisSnapshot(
            repository_id=self.repo1.id,
            scanned_at=now - timedelta(days=2),
            total_findings=10,
            errors=2,
            warnings=5,
            info=3,
        )
        snap2 = AnalysisSnapshot(
            repository_id=self.repo1.id,
            scanned_at=now - timedelta(days=1),
            total_findings=15,
            errors=3,
            warnings=10,
            info=2,
        )
        snap3 = AnalysisSnapshot(
            repository_id=self.repo2.id,
            scanned_at=now - timedelta(hours=5),
            total_findings=5,
            errors=0,
            warnings=5,
            info=0,
        )
        snap4 = AnalysisSnapshot(
            repository_id=self.repo3.id,
            scanned_at=now - timedelta(hours=1),
            total_findings=100,
            errors=100,
            warnings=0,
            info=0,
        )
        
        self.db.add_all([snap1, snap2, snap3, snap4])
        self.db.commit()

        response = self.client.get("/dashboard/findings-trend")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(len(data), 3)
        
        self.assertEqual(data[0]["findings"], 10)
        self.assertEqual(data[0]["errors"], 2)
        
        self.assertEqual(data[1]["findings"], 15)
        self.assertEqual(data[2]["findings"], 5)
        
        for row in data:
            self.assertNotEqual(row["findings"], 100)

    def test_unauthenticated(self):
        app.dependency_overrides.clear()
        response = self.client.get("/dashboard/findings-trend")
        self.assertEqual(response.status_code, 401)
