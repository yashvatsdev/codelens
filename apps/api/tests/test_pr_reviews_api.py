import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.database import SessionLocal
from app.models.user import User
from app.models.repository import Repository
from app.models.pr_review import PRReview
from tests.conftest import FAKE_USER, get_current_user, app

def override_get_current_user():
    return FAKE_USER

client = TestClient(app)

class TestPRReviewsEndpoints(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        self.client = client
        self.db = SessionLocal()
        
        # Setup Test User 1
        self.user1 = self.db.query(User).filter_by(id=1).first()
        if not self.user1:
            self.user1 = User(id=1, email="test@codelens.test", password_hash="x", name="Test 1")
            self.db.add(self.user1)
            self.db.commit()
            
        # Setup Test User 2
        self.user2 = self.db.query(User).filter_by(id=2).first()
        if not self.user2:
            self.user2 = User(id=2, email="test2@example.com", password_hash="x", name="Test 2")
            self.db.add(self.user2)
            self.db.commit()
            
        # Setup Repository for User 1
        self.repo1 = self.db.query(Repository).filter_by(id=101).first()
        if not self.repo1:
            self.repo1 = Repository(
                id=101, user_id=self.user1.id, github_id="repo123", name="repo1", owner="org", full_name="org/repo1", url="url"
            )
            self.db.add(self.repo1)
            self.db.commit()
            
        # Setup PR Reviews
        self._cleanup()
        
        self.review1 = PRReview(
            user_id=self.user1.id,
            repository_id=self.repo1.id,
            pull_request_number=1,
            summary="summary 1",
            risk_level="HIGH",
            overall_assessment="assessment 1",
            key_findings=[{"issue": "x", "severity": "error", "category": "bug", "file_path": "a", "recommendation": "y", "impact": "z"}],
            recommendations=["rec 1"]
        )
        self.review2 = PRReview(
            user_id=self.user2.id,
            repository_id=self.repo1.id,  # User 2 shouldn't own this logically, but let's just make it separate
            pull_request_number=2,
            summary="summary 2",
            risk_level="LOW",
            overall_assessment="assessment 2",
            key_findings=[],
            recommendations=[]
        )
        self.db.add_all([self.review1, self.review2])
        self.db.commit()
        self.db.refresh(self.review1)
        self.db.refresh(self.review2)

    def tearDown(self):
        self._cleanup()
        self.db.close()
        app.dependency_overrides.clear()

    def _cleanup(self):
        self.db.query(PRReview).delete()
        self.db.commit()

    def test_get_pr_review_history_success(self):
        # By default get_current_user returns user 1
        response = self.client.get("/pr-reviews")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # User 1 should only see review1
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], self.review1.id)
        self.assertEqual(data[0]["pull_request_number"], 1)
        self.assertEqual(data[0]["risk_level"], "HIGH")
        self.assertEqual(data[0]["findings_count"], 1)

    def test_get_pr_review_detail_success(self):
        response = self.client.get(f"/pr-reviews/{self.review1.id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], self.review1.id)
        self.assertEqual(data["overall_assessment"], "assessment 1")
        self.assertEqual(len(data["key_findings"]), 1)

    def test_get_pr_review_detail_not_found_or_forbidden(self):
        # User 1 trying to access User 2's review
        response = self.client.get(f"/pr-reviews/{self.review2.id}")
        self.assertEqual(response.status_code, 404)
