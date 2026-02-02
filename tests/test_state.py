"""Tests for state management (SQLite-backed polecat state)."""

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polecat_supervisor.state import PolecatState, PolecatRecord


class TestPolecatState:
    """Tests for PolecatState class."""

    def test_create_polecat_generates_unique_id(self, temp_db: Path, temp_workdir: Path):
        """Each created polecat should have a unique ID."""
        state = PolecatState(str(temp_db))
        ids = set()
        for _ in range(10):
            polecat_id = state.create(str(temp_workdir), "test task", "Read,Write")
            ids.add(polecat_id)
        assert len(ids) == 10, "All IDs should be unique"

    def test_create_polecat_sets_pending_status(self, temp_db: Path, temp_workdir: Path):
        """Newly created polecats should have 'pending' status."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "test task", "Read,Write")
        record = state.get(polecat_id)
        assert record is not None
        assert record.status == "pending"

    def test_get_returns_none_for_missing(self, temp_db: Path):
        """Getting a non-existent polecat should return None."""
        state = PolecatState(str(temp_db))
        result = state.get("nonexistent-id-12345")
        assert result is None

    def test_get_returns_record_for_existing(self, temp_db: Path, temp_workdir: Path):
        """Getting an existing polecat should return a PolecatRecord."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "my task", "Read")
        record = state.get(polecat_id)
        assert record is not None
        assert isinstance(record, PolecatRecord)
        assert record.id == polecat_id
        assert record.workdir == str(temp_workdir)
        assert record.task == "my task"
        assert record.allowed_tools == "Read"

    def test_list_returns_all_by_default(self, temp_db: Path, temp_workdir: Path):
        """list() without filters should return all polecats."""
        state = PolecatState(str(temp_db))
        state.create(str(temp_workdir), "task1", "Read")
        state.create(str(temp_workdir), "task2", "Write")
        state.create(str(temp_workdir), "task3", "Read,Write")
        
        records = state.list()
        assert len(records) == 3

    def test_list_filters_by_status(self, temp_db: Path, temp_workdir: Path):
        """list(status=...) should filter by status."""
        state = PolecatState(str(temp_db))
        id1 = state.create(str(temp_workdir), "task1", "Read")
        id2 = state.create(str(temp_workdir), "task2", "Read")
        id3 = state.create(str(temp_workdir), "task3", "Read")
        
        state.update_status(id1, "running")
        state.update_status(id2, "completed")
        
        pending = state.list(status="pending")
        running = state.list(status="running")
        completed = state.list(status="completed")
        
        assert len(pending) == 1
        assert len(running) == 1
        assert len(completed) == 1
        assert pending[0].id == id3
        assert running[0].id == id1
        assert completed[0].id == id2

    def test_update_status_changes_status(self, temp_db: Path, temp_workdir: Path):
        """update_status should change the status field."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        
        state.update_status(polecat_id, "running")
        record = state.get(polecat_id)
        assert record.status == "running"
        
        state.update_status(polecat_id, "completed")
        record = state.get(polecat_id)
        assert record.status == "completed"

    def test_update_status_sets_timestamps(self, temp_db: Path, temp_workdir: Path):
        """update_status should set started_at and finished_at appropriately."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        
        # Initially no timestamps
        record = state.get(polecat_id)
        assert record.started_at is None
        assert record.finished_at is None
        
        # Running sets started_at
        state.update_status(polecat_id, "running")
        record = state.get(polecat_id)
        assert record.started_at is not None
        assert record.finished_at is None
        
        # Completed sets finished_at
        state.update_status(polecat_id, "completed")
        record = state.get(polecat_id)
        assert record.started_at is not None
        assert record.finished_at is not None

    def test_update_status_preserves_other_fields(self, temp_db: Path, temp_workdir: Path):
        """update_status should not change unrelated fields."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "original task", "Read,Write")
        
        state.update_status(polecat_id, "running", pid=12345)
        state.update_status(polecat_id, "completed", exit_code=0)
        
        record = state.get(polecat_id)
        assert record.task == "original task"
        assert record.allowed_tools == "Read,Write"
        assert record.pid == 12345
        assert record.exit_code == 0

    def test_cleanup_removes_old_completed(self, temp_db: Path, temp_workdir: Path):
        """cleanup_old should remove completed polecats older than threshold."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "old task", "Read")
        state.update_status(polecat_id, "completed")
        
        # Manually backdate the finished_at
        conn = sqlite3.connect(str(temp_db))
        old_time = (datetime.now() - timedelta(days=10)).isoformat()
        conn.execute("UPDATE polecats SET finished_at = ? WHERE id = ?", (old_time, polecat_id))
        conn.commit()
        conn.close()
        
        state.cleanup_old(days=7)
        assert state.get(polecat_id) is None

    def test_cleanup_preserves_recent(self, temp_db: Path, temp_workdir: Path):
        """cleanup_old should preserve recently completed polecats."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "recent task", "Read")
        state.update_status(polecat_id, "completed")
        
        state.cleanup_old(days=7)
        assert state.get(polecat_id) is not None

    def test_cleanup_preserves_running(self, temp_db: Path, temp_workdir: Path):
        """cleanup_old should never remove running polecats."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "running task", "Read")
        state.update_status(polecat_id, "running")
        
        # Manually backdate started_at
        conn = sqlite3.connect(str(temp_db))
        old_time = (datetime.now() - timedelta(days=30)).isoformat()
        conn.execute("UPDATE polecats SET started_at = ? WHERE id = ?", (old_time, polecat_id))
        conn.commit()
        conn.close()
        
        state.cleanup_old(days=7)
        assert state.get(polecat_id) is not None

    def test_concurrent_creates_no_collision(self, temp_db: Path, temp_workdir: Path):
        """Concurrent creates should not cause ID collisions."""
        state = PolecatState(str(temp_db))
        ids = []
        errors = []
        
        def create_polecat():
            try:
                polecat_id = state.create(str(temp_workdir), "concurrent task", "Read")
                ids.append(polecat_id)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=create_polecat) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0, f"Errors during concurrent creates: {errors}"
        assert len(set(ids)) == 20, "All IDs should be unique"

    def test_db_persists_across_instances(self, temp_db: Path, temp_workdir: Path):
        """State should persist when creating new PolecatState instances."""
        state1 = PolecatState(str(temp_db))
        polecat_id = state1.create(str(temp_workdir), "persistent task", "Read")
        del state1
        
        state2 = PolecatState(str(temp_db))
        record = state2.get(polecat_id)
        assert record is not None
        assert record.task == "persistent task"

    def test_handles_missing_db_directory(self, temp_dir: Path):
        """PolecatState should create missing directories for DB path."""
        nested_path = temp_dir / "nested" / "dirs" / "state.db"
        state = PolecatState(str(nested_path))
        polecat_id = state.create("/tmp", "task", "Read")
        assert state.get(polecat_id) is not None

    def test_record_has_log_path(self, temp_db: Path, temp_workdir: Path):
        """Created records should have a log_path set."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        record = state.get(polecat_id)
        assert record.log_path is not None
        assert len(record.log_path) > 0

    def test_update_with_error_message(self, temp_db: Path, temp_workdir: Path):
        """update_status should allow setting error_message."""
        state = PolecatState(str(temp_db))
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        
        state.update_status(polecat_id, "failed", error_message="Something went wrong")
        record = state.get(polecat_id)
        assert record.status == "failed"
        assert record.error_message == "Something went wrong"
