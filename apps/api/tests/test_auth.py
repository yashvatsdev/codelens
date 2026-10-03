import unittest
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import SessionLocal
from app.models.user import User
from app.core.security import get_password_hash

class TestAuth(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()
        self.client = TestClient(app)
        
    def tearDown(self):
        self._cleanup()
        self.db.close()
        
    def _cleanup(self):
        self.db.query(User).filter(User.email.in_([
            "google_only@example.com", 
            "g1@example.com", 
            "g2@example.com", 
            "unique@example.com",
            "nopass@example.com",
            "haspass@example.com"
        ])).delete()
        self.db.commit()

    def test_user_can_have_null_password_and_google_sub(self):
        user = User(
            email="google_only@example.com",
            password_hash=None,
            google_sub="google-123",
            name="Google User"
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        self.assertIsNotNone(user.id)
        self.assertIsNone(user.password_hash)
        self.assertEqual(user.google_sub, "google-123")

    def test_google_sub_must_be_unique(self):
        user1 = User(
            email="g1@example.com",
            password_hash=None,
            google_sub="sub-same"
        )
        self.db.add(user1)
        self.db.commit()

        user2 = User(
            email="g2@example.com",
            password_hash=None,
            google_sub="sub-same"
        )
        self.db.add(user2)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_existing_email_uniqueness_enforced(self):
        user1 = User(email="unique@example.com", password_hash="123")
        self.db.add(user1)
        self.db.commit()

        user2 = User(email="unique@example.com", password_hash="456")
        self.db.add(user2)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_password_login_with_null_password_fails_safely(self):
        user = User(email="nopass@example.com", password_hash=None, google_sub="sub123")
        self.db.add(user)
        self.db.commit()

        response = self.client.post("/auth/login", json={
            "email": "nopass@example.com",
            "password": "somepassword"
        })
        
        self.assertEqual(response.status_code, 401)
        self.assertIn("Incorrect email or password", response.json()["detail"])

    def test_password_login_succeeds(self):
        user = User(email="haspass@example.com", password_hash=get_password_hash("realpass"))
        self.db.add(user)
        self.db.commit()

        response = self.client.post("/auth/login", json={
            "email": "haspass@example.com",
            "password": "realpass"
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "haspass@example.com")
