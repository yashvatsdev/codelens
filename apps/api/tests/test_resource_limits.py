"""Tests for repository resource exhaustion protection."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.github import (
    GitHubTreeEntry,
    GitHubFileContent,
    RepositoryTooLargeError,
    MAX_TREE_ENTRIES,
    MAX_FILE_SIZE_BYTES,
    fetch_repo_tree,
    is_supported_file,
)
from app.services.ingestion import (
    ingest_repository,
    RepositorySourceTooLargeError,
    MAX_TOTAL_SOURCE_BYTES,
)
from app.services.code_retriever import (
    _find_best_window,
    MAX_FILES,
    MAX_LINES_PER_FILE,
    MAX_TOTAL_LINES,
)
from app.api.deps import get_current_user
from tests.conftest import FAKE_USER


client = TestClient(app)


def test_tree_size_protection():
    """Tree with > MAX_TREE_ENTRIES is rejected before fetching files."""
    large_tree = [{"path": f"file_{i}.py", "type": "blob", "sha": "123", "size": 100} for i in range(MAX_TREE_ENTRIES + 1)]
    
    with patch("app.services.github._github_api_request", return_value={"tree": large_tree}):
        with pytest.raises(RepositoryTooLargeError) as exc:
            fetch_repo_tree("owner", "repo")
        assert f"exceeds the maximum of {MAX_TREE_ENTRIES}" in str(exc.value)


def test_aggregate_source_size_protection_before_fetch():
    """Ingestion rejects repository if sum of supported file sizes exceeds MAX_TOTAL_SOURCE_BYTES."""
    file_size = 50_000  # valid single file
    file_count = (MAX_TOTAL_SOURCE_BYTES // file_size) + 1  # enough to exceed total
    
    large_tree = [
        GitHubTreeEntry(path=f"file_{i}.py", type="blob", sha="123", size=file_size)
        for i in range(file_count)
    ]
    
    with patch("app.services.ingestion.fetch_repo_tree", return_value=large_tree):
        with pytest.raises(RepositorySourceTooLargeError) as exc:
            ingest_repository("owner", "repo", max_files=file_count)
        assert "exceeding the maximum allowed size" in str(exc.value)


def test_aggregate_source_size_protection_during_fetch():
    """Ingestion rejects repository if actual fetched size exceeds MAX_TOTAL_SOURCE_BYTES."""
    # GitHub tree metadata says files are tiny (so it passes the first check)
    file_count = 500
    large_tree = [
        GitHubTreeEntry(path=f"file_{i}.py", type="blob", sha="123", size=10)
        for i in range(file_count)
    ]
    
    # But the actual fetch returns huge content, leading to aggregate overflow
    actual_file_size = (MAX_TOTAL_SOURCE_BYTES // 10) + 1000
    large_content = "a" * actual_file_size
    
    with patch("app.services.ingestion.fetch_repo_tree", return_value=large_tree), \
         patch("app.services.ingestion.fetch_file_content", return_value=GitHubFileContent(path="", sha="", content=large_content, size=actual_file_size)):
        
        with pytest.raises(RepositorySourceTooLargeError) as exc:
            ingest_repository("owner", "repo", max_files=file_count)
        assert "exceeds the maximum allowed size" in str(exc.value)


def test_file_count_protection():
    """Ingestion fetches exactly up to max_files, no more."""
    # Create 300 supported files
    large_tree = [
        GitHubTreeEntry(path=f"file_{i}.py", type="blob", sha="123", size=10)
        for i in range(300)
    ]
    
    with patch("app.services.ingestion.fetch_repo_tree", return_value=large_tree), \
         patch("app.services.ingestion.fetch_file_content", side_effect=lambda o, r, p: GitHubFileContent(path=p, sha="123", content="print('hello')", size=14)) as mock_fetch:
        
        result = ingest_repository("owner", "repo", max_files=200)
        
        assert mock_fetch.call_count == 200
        assert result.files_identified == 300
        assert result.files_fetched == 200
        assert result.files_skipped == 100


def test_individual_file_size_limit():
    """is_supported_file rejects files larger than MAX_FILE_SIZE_BYTES."""
    assert is_supported_file("test.py", size=100) is True
    assert is_supported_file("test.py", size=MAX_FILE_SIZE_BYTES + 1) is False


def test_endpoint_returns_413_for_large_tree():
    """POST /repositories/{id}/ingest returns 413 for RepositoryTooLargeError."""
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    
    with patch("app.api.routes.repositories.Session.get", return_value=MagicMock(user_id=FAKE_USER.id, owner="owner", name="repo", default_branch="main")), \
         patch("app.api.routes.repositories.ingest_repository", side_effect=RepositoryTooLargeError("Too large")):
        
        response = client.post("/repositories/1/ingest")
        
        assert response.status_code == 413
        assert response.json()["detail"] == "Too large"
        
    app.dependency_overrides.clear()


def test_scan_concurrency_protection():
    """POST /repositories/{id}/scan returns existing state if scan is active."""
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    
    mock_running_state = {
        "repository_id": 1,
        "status": "running",
        "stage": "static_analysis",
        "progress": 50,
        "files_processed": 0,
        "files_total": 0,
        "message": "Starting static code analysis..."
    }
    
    with patch("app.api.routes.repositories.Session.get", return_value=MagicMock(user_id=FAKE_USER.id)), \
         patch("app.api.routes.repositories.claim_scan", return_value=(False, mock_running_state)), \
         patch("app.api.routes.repositories.BackgroundTasks.add_task") as mock_add_task:
        
        response = client.post("/repositories/1/scan")
        
        assert response.status_code == 202
        assert response.json()["status"] == "running"
        mock_add_task.assert_not_called()
        
    app.dependency_overrides.clear()


def test_ask_codelens_protections():
    """Ask CodeLens limits are maintained."""
    assert MAX_FILES == 5
    assert MAX_LINES_PER_FILE == 200
    assert MAX_TOTAL_LINES == 1000
    
    # Test _find_best_window bounds
    start, end = _find_best_window(match_lines=[500], total_lines=1000, window_size=MAX_LINES_PER_FILE)
    assert end - start <= MAX_LINES_PER_FILE
