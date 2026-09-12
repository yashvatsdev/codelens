"""Thread-safe in-memory repository scan progress service."""

from __future__ import annotations

import threading
from typing import Any


_lock = threading.Lock()
_scan_store: dict[int, dict[str, Any]] = {}


def _default_idle_state(repository_id: int) -> dict[str, Any]:
    return {
        "repository_id": repository_id,
        "status": "idle",
        "stage": "idle",
        "progress": 0,
        "files_processed": 0,
        "files_total": 0,
        "message": "No scan in progress",
    }


def get_scan_progress(repository_id: int) -> dict[str, Any]:
    """Retrieve the current scan progress for a repository.

    Returns a copy of the active or completed progress dictionary,
    or a default idle state if no scan has been recorded.
    """
    with _lock:
        state = _scan_store.get(repository_id)
        if state is None:
            return _default_idle_state(repository_id)
        return dict(state)


def set_scan_progress(
    repository_id: int,
    status: str,
    stage: str,
    progress: int,
    files_processed: int = 0,
    files_total: int = 0,
    message: str = "",
) -> dict[str, Any]:
    """Set the full scan progress state for a repository."""
    clamped_progress = max(0, min(100, int(progress)))
    new_state: dict[str, Any] = {
        "repository_id": repository_id,
        "status": status,
        "stage": stage,
        "progress": clamped_progress,
        "files_processed": max(0, int(files_processed)),
        "files_total": max(0, int(files_total)),
        "message": message,
    }
    with _lock:
        _scan_store[repository_id] = new_state
        return dict(new_state)


def update_scan_progress(repository_id: int, **kwargs: Any) -> dict[str, Any]:
    """Update specific fields of an existing scan progress state."""
    with _lock:
        current = _scan_store.get(repository_id)
        if current is None:
            current = _default_idle_state(repository_id)
        else:
            current = dict(current)

        for key, value in kwargs.items():
            if key == "progress":
                current["progress"] = max(0, min(100, int(value)))
            elif key in ("files_processed", "files_total"):
                current[key] = max(0, int(value))
            else:
                current[key] = value

        _scan_store[repository_id] = current
        return dict(current)


def is_scan_active(repository_id: int) -> bool:
    """Check whether a scan is currently queued or running for a repository."""
    with _lock:
        state = _scan_store.get(repository_id)
        if state is None:
            return False
        return state.get("status") in ("queued", "running")


def clear_scan_progress(repository_id: int | None = None) -> None:
    """Clear progress state for a specific repository or all repositories (for test resets)."""
    with _lock:
        if repository_id is not None:
            _scan_store.pop(repository_id, None)
        else:
            _scan_store.clear()

