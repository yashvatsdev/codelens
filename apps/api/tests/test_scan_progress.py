"""Unit and integration tests for real-time repository scan progress."""

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.main import app
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.services.analyzer import analyze_repository
from app.services.ingestion import IngestionResult, ingest_repository
from app.services.scan_progress import (
    clear_scan_progress,
    get_scan_progress,
    is_scan_active,
    set_scan_progress,
    update_scan_progress,
)


class TestScanProgressService(unittest.TestCase):
    """Unit tests for the in-memory scan progress service."""

    def setUp(self):
        clear_scan_progress()

    def tearDown(self):
        clear_scan_progress()

    def test_default_idle_state(self):
        state = get_scan_progress(101)
        self.assertEqual(state["repository_id"], 101)
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["stage"], "idle")
        self.assertEqual(state["progress"], 0)
        self.assertFalse(is_scan_active(101))

    def test_set_and_update_progress(self):
        state = set_scan_progress(
            repository_id=101,
            status="running",
            stage="fetching_files",
            progress=25,
            files_processed=5,
            files_total=20,
            message="Fetching files...",
        )
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["progress"], 25)
        self.assertTrue(is_scan_active(101))

        updated = update_scan_progress(101, progress=60, files_processed=12)
        self.assertEqual(updated["progress"], 60)
        self.assertEqual(updated["files_processed"], 12)
        self.assertEqual(updated["stage"], "fetching_files")

    def test_progress_clamping_between_0_and_100(self):
        state_neg = set_scan_progress(101, "running", "preparing", -10)
        self.assertEqual(state_neg["progress"], 0)

        state_over = update_scan_progress(101, progress=150)
        self.assertEqual(state_over["progress"], 100)

    def test_multiple_repositories_maintain_independent_progress(self):
        set_scan_progress(101, "running", "fetching_files", 30, 3, 10)
        set_scan_progress(202, "running", "static_analysis", 75, 15, 20)

        state1 = get_scan_progress(101)
        state2 = get_scan_progress(202)

        self.assertEqual(state1["stage"], "fetching_files")
        self.assertEqual(state1["progress"], 30)
        self.assertEqual(state2["stage"], "static_analysis")
        self.assertEqual(state2["progress"], 75)

    def test_is_scan_active_behavior(self):
        set_scan_progress(101, "queued", "preparing", 0)
        self.assertTrue(is_scan_active(101))

        set_scan_progress(101, "running", "static_analysis", 50)
        self.assertTrue(is_scan_active(101))

        set_scan_progress(101, "completed", "completed", 100)
        self.assertFalse(is_scan_active(101))

        set_scan_progress(101, "failed", "failed", 0)
        self.assertFalse(is_scan_active(101))


class TestScanCallbacks(unittest.TestCase):
    """Unit tests verifying progress callbacks in ingestion and analyzer services."""

    def test_ingest_repository_triggers_callback(self):
        callback_calls = []

        def dummy_cb(processed, total, msg):
            callback_calls.append((processed, total, msg))

        mock_entries = [
            MagicMock(type="blob", path="app.py", size=100),
            MagicMock(type="blob", path="utils.py", size=150),
        ]
        mock_content = MagicMock(path="app.py", sha="sha1", size=100, content="print('hello')")

        with patch("app.services.ingestion.fetch_repo_tree", return_value=mock_entries), \
             patch("app.services.ingestion.is_supported_file", return_value=True), \
             patch("app.services.ingestion.fetch_file_content", return_value=mock_content):
            result = ingest_repository(
                owner="org",
                repo="repo",
                branch="main",
                progress_callback=dummy_cb,
            )

        self.assertEqual(result.files_fetched, 2)
        # Should have called callback for initial count (0) and for each of the 2 files
        self.assertGreaterEqual(len(callback_calls), 3)
        self.assertEqual(callback_calls[0][0], 0)
        self.assertEqual(callback_calls[-1][0], 2)
        self.assertEqual(callback_calls[-1][1], 2)

    def test_analyze_repository_triggers_callback(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        repo = Repository(
            github_id="test-repo-1",
            name="test-repo",
            owner="org",
            full_name="org/test-repo",
            url="https://github.com/org/test-repo",
            default_branch="main",
        )
        db.add(repo)
        db.commit()

        sf1 = SourceFile(repository_id=repo.id, path="a.py", sha="sha1", content="x = 1\n", size=6)
        sf2 = SourceFile(repository_id=repo.id, path="b.py", sha="sha2", content="y = 2\n", size=6)
        db.add_all([sf1, sf2])
        db.commit()

        callback_calls = []

        def dummy_cb(processed, total, msg):
            callback_calls.append((processed, total, msg))

        result = analyze_repository(
            repository_id=repo.id,
            db=db,
            progress_callback=dummy_cb,
        )

        self.assertEqual(result.files_analyzed, 2)
        self.assertGreaterEqual(len(callback_calls), 3)
        self.assertEqual(callback_calls[0][0], 0)
        self.assertEqual(callback_calls[-1][0], 2)
        self.assertEqual(callback_calls[-1][1], 2)
        db.close()


class TestScanEndpointsIntegration(unittest.TestCase):
    """Integration tests for POST /repositories/{id}/scan and GET /repositories/{id}/scan-status."""

    def setUp(self):
        clear_scan_progress()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        self.repo = Repository(
            github_id="scan-repo-1",
            name="scan-repo",
            owner="scan-org",
            full_name="scan-org/scan-repo",
            url="https://github.com/scan-org/scan-repo",
            default_branch="main",
        )
        self.db.add(self.repo)
        self.db.commit()
        self.db.refresh(self.repo)

    def tearDown(self):
        clear_scan_progress()
        app.dependency_overrides.clear()
        self.db.close()

    def test_get_scan_status_not_found_repo(self):
        resp = self.client.get("/repositories/99999/scan-status")
        self.assertEqual(resp.status_code, 404)

    def test_get_scan_status_idle_initially(self):
        resp = self.client.get(f"/repositories/{self.repo.id}/scan-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["repository_id"], self.repo.id)
        self.assertEqual(data["status"], "idle")
        self.assertEqual(data["progress"], 0)

    @patch("app.api.routes.repositories.run_background_scan")
    def test_post_scan_returns_immediately_and_enqueues_background_task(self, mock_bg_scan):
        """POST /scan must return 202 without waiting for the full scan."""
        resp = self.client.post(f"/repositories/{self.repo.id}/scan")
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data["repository_id"], self.repo.id)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["stage"], "preparing")
        mock_bg_scan.assert_called_once_with(self.repo.id)

    def test_duplicate_scan_prevention(self):
        """Starting a scan when one is already active returns existing status and does not start duplicate."""
        set_scan_progress(self.repo.id, status="running", stage="fetching_files", progress=35)

        with patch("app.api.routes.repositories.run_background_scan") as mock_bg_scan:
            resp = self.client.post(f"/repositories/{self.repo.id}/scan")
            self.assertEqual(resp.status_code, 202)
            data = resp.json()
            self.assertEqual(data["status"], "running")
            self.assertEqual(data["stage"], "fetching_files")
            self.assertEqual(data["progress"], 35)
            mock_bg_scan.assert_not_called()

    def test_run_background_scan_success_flow(self):
        """run_background_scan executes ingestion and analysis, setting progress to 100%."""
        from app.api.routes.repositories import run_background_scan

        with patch("app.api.routes.repositories.SessionLocal", return_value=self.db), \
             patch("app.api.routes.repositories.ingest_repository") as mock_ingest, \
             patch("app.api.routes.repositories.analyze_repository") as mock_analyze:

            mock_ingest.return_value = IngestionResult(
                owner="scan-org", repo="scan-repo", branch="main",
                total_tree_entries=5, files_identified=2, files_fetched=2, files_skipped=0, files_stored=2,
            )
            mock_analyze.return_value = MagicMock(files_analyzed=2, total_findings=3)

            run_background_scan(self.repo.id)

            status = get_scan_progress(self.repo.id)
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["stage"], "completed")
            self.assertEqual(status["progress"], 100)
            self.assertEqual(status["files_processed"], 2)
            self.assertIn("Scan completed", status["message"])

    def test_run_background_scan_failure_flow(self):
        """When background scan encounters an error, it sets failed status with clean message."""
        from app.api.routes.repositories import run_background_scan

        with patch("app.api.routes.repositories.SessionLocal", return_value=self.db), \
             patch("app.api.routes.repositories.ingest_repository", side_effect=RuntimeError("GitHub API rate limit exceeded")):

            run_background_scan(self.repo.id)

            status = get_scan_progress(self.repo.id)
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["stage"], "failed")
            self.assertEqual(status["progress"], 0)
            self.assertNotIn("Traceback", status["message"])
            self.assertIn("rate limit", status["message"].lower())
