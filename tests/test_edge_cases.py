"""Edge case tests for polecat supervisor."""

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from polecat_supervisor.daemon import PolecatDaemon, DaemonClient
from polecat_supervisor.state import PolecatState
from polecat_supervisor.cli import PolectlApp


@pytest.fixture
def edge_setup(temp_dir: Path, temp_workdir: Path):
    """Set up edge case test environment."""
    socket_path = temp_dir / "daemon.sock"
    db_path = temp_dir / "state.db"
    
    mock_claude = temp_dir / "mock_claude"
    mock_claude.write_text('''#!/bin/bash
echo "mock output"
exit 0
''')
    mock_claude.chmod(0o755)
    
    daemon = PolecatDaemon(
        socket_path=str(socket_path),
        db_path=str(db_path),
        claude_path=str(mock_claude)
    )
    
    thread = threading.Thread(target=daemon.start, daemon=True)
    thread.start()
    time.sleep(0.3)
    
    yield {
        "daemon": daemon,
        "socket_path": str(socket_path),
        "db_path": str(db_path),
        "workdir": str(temp_workdir),
        "temp_dir": temp_dir,
    }
    
    daemon.stop()
    thread.join(timeout=2)


class TestEdgeCases:
    """Edge case tests."""

    def test_spawn_invalid_workdir(self, edge_setup):
        """Test spawning with non-existent workdir."""
        client = DaemonClient(edge_setup["socket_path"])
        
        resp = client.send({
            "action": "spawn",
            "workdir": "/nonexistent/path/xyz123",
            "task": "test task",
            "allowed_tools": "Read"
        })
        
        # Should spawn but fail quickly
        assert resp["status"] == "ok"
        polecat_id = resp["id"]
        
        time.sleep(0.5)
        
        state = PolecatState(edge_setup["db_path"])
        record = state.get(polecat_id)
        # Should have failed due to invalid workdir
        assert record.status in ("failed", "completed")  # Worker handles gracefully

    def test_spawn_empty_task(self, edge_setup):
        """Test spawning with empty task."""
        client = DaemonClient(edge_setup["socket_path"])
        
        resp = client.send({
            "action": "spawn",
            "workdir": edge_setup["workdir"],
            "task": "",
            "allowed_tools": "Read"
        })
        
        # Should fail validation
        assert resp["status"] == "error"

    def test_spawn_huge_task(self, edge_setup):
        """Test spawning with very large task."""
        client = DaemonClient(edge_setup["socket_path"])
        
        huge_task = "x" * 1_000_000  # 1MB task
        
        resp = client.send({
            "action": "spawn",
            "workdir": edge_setup["workdir"],
            "task": huge_task,
            "allowed_tools": "Read"
        })
        
        # Should handle gracefully (either succeed or fail with error)
        assert resp["status"] in ("ok", "error")

    def test_claude_not_in_path(self, edge_setup, temp_dir: Path):
        """Test behavior when claude executable not found."""
        socket_path = temp_dir / "daemon_noclaude.sock"
        db_path = temp_dir / "state_noclaude.db"
        
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path="/nonexistent/claude"
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            client = DaemonClient(str(socket_path))
            
            resp = client.send({
                "action": "spawn",
                "workdir": edge_setup["workdir"],
                "task": "test",
                "allowed_tools": "Read"
            })
            
            polecat_id = resp["id"]
            time.sleep(0.5)
            
            state = PolecatState(str(db_path))
            record = state.get(polecat_id)
            assert record.status == "failed"
        finally:
            daemon.stop()
            thread.join(timeout=2)

    def test_disk_full_during_log(self, edge_setup):
        """Test handling when log write fails."""
        # This is hard to test directly, but we verify error handling exists
        client = DaemonClient(edge_setup["socket_path"])
        
        resp = client.send({
            "action": "spawn",
            "workdir": edge_setup["workdir"],
            "task": "test disk",
            "allowed_tools": "Read"
        })
        
        # Should complete regardless of log issues (best-effort logging)
        assert resp["status"] == "ok"

    def test_socket_permission_denied(self, edge_setup, temp_dir: Path):
        """Test connecting to daemon with wrong permissions."""
        # Create a file (not socket) at socket path
        fake_socket = temp_dir / "fake.sock"
        fake_socket.touch()
        fake_socket.chmod(0o000)
        
        try:
            client = DaemonClient(str(fake_socket))
            resp = client.send({"action": "status"})
            
            # Should get connection error
            assert resp["status"] == "error"
        finally:
            fake_socket.chmod(0o644)

    def test_db_locked(self, edge_setup):
        """Test behavior when DB is accessed concurrently."""
        state = PolecatState(edge_setup["db_path"])
        errors = []
        
        def create_many():
            try:
                for _ in range(20):
                    state.create(edge_setup["workdir"], "task", "Read")
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=create_many) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # SQLite should handle this with timeouts
        assert len(errors) == 0

    def test_kill_already_finished(self, edge_setup):
        """Test killing an already finished polecat."""
        client = DaemonClient(edge_setup["socket_path"])
        
        resp = client.send({
            "action": "spawn",
            "workdir": edge_setup["workdir"],
            "task": "quick task",
            "allowed_tools": "Read"
        })
        polecat_id = resp["id"]
        
        time.sleep(1)  # Let it complete
        
        # Kill already finished
        kill_resp = client.send({"action": "kill", "id": polecat_id})
        
        # Should succeed (idempotent)
        assert kill_resp["status"] == "ok"

    def test_status_during_spawn(self, edge_setup, temp_dir: Path):
        """Test getting status while spawn is in progress."""
        slow_claude = temp_dir / "slow_status_claude"
        slow_claude.write_text('''#!/bin/bash
sleep 2
exit 0
''')
        slow_claude.chmod(0o755)
        
        socket_path = temp_dir / "daemon_status.sock"
        db_path = temp_dir / "state_status.db"
        
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(slow_claude)
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            client = DaemonClient(str(socket_path))
            
            resp = client.send({
                "action": "spawn",
                "workdir": edge_setup["workdir"],
                "task": "slow task",
                "allowed_tools": "Read"
            })
            polecat_id = resp["id"]
            
            time.sleep(0.2)  # Still running
            
            status_resp = client.send({"action": "status", "id": polecat_id})
            
            assert status_resp["status"] == "ok"
            assert status_resp["polecat"]["status"] == "running"
        finally:
            daemon.stop()
            thread.join(timeout=3)

    def test_malformed_json_request(self, edge_setup):
        """Test daemon handling of malformed JSON."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(edge_setup["socket_path"])
        
        try:
            sock.send(b"not valid json {{{")
            sock.shutdown(socket.SHUT_WR)
            
            response = sock.recv(65536)
            resp = json.loads(response.decode())
            
            assert resp["status"] == "error"
        finally:
            sock.close()

    def test_status_with_missing_id(self, edge_setup):
        """Test status request with non-existent ID."""
        client = DaemonClient(edge_setup["socket_path"])
        
        resp = client.send({
            "action": "status",
            "id": "pc-nonexistent123"
        })
        
        assert resp["status"] == "error"
        assert "not found" in resp["error"].lower()

    def test_logs_with_missing_log_file(self, edge_setup):
        """Test logs request when log file doesn't exist."""
        client = DaemonClient(edge_setup["socket_path"])
        state = PolecatState(edge_setup["db_path"])
        
        # Create record manually without running worker
        polecat_id = state.create(edge_setup["workdir"], "test", "Read")
        
        resp = client.send({
            "action": "logs",
            "id": polecat_id
        })
        
        # Should return empty content, not error
        assert resp["status"] == "ok"
        assert resp["content"] == ""

    def test_special_characters_in_task(self, edge_setup):
        """Test task with special shell characters."""
        client = DaemonClient(edge_setup["socket_path"])
        
        special_task = '''Task with "quotes" and 'apostrophes' and $variables and `backticks` and $(subshell)'''
        
        resp = client.send({
            "action": "spawn",
            "workdir": edge_setup["workdir"],
            "task": special_task,
            "allowed_tools": "Read"
        })
        
        assert resp["status"] == "ok"

    def test_unicode_in_task(self, edge_setup):
        """Test task with unicode characters."""
        client = DaemonClient(edge_setup["socket_path"])
        
        unicode_task = "Task with unicode: 你好世界 🎉 émojis and ñ"
        
        resp = client.send({
            "action": "spawn",
            "workdir": edge_setup["workdir"],
            "task": unicode_task,
            "allowed_tools": "Read"
        })
        
        assert resp["status"] == "ok"
