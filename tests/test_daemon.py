"""Tests for polecat daemon (Unix socket server)."""

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from polecat_supervisor.daemon import PolecatDaemon, DaemonClient
from polecat_supervisor.state import PolecatState
from polecat_supervisor.limits import LimitConfig


@pytest.fixture
def daemon_setup(temp_dir: Path, temp_workdir: Path):
    """Set up daemon with temp paths."""
    socket_path = temp_dir / "daemon.sock"
    db_path = temp_dir / "state.db"
    
    # Create mock claude
    mock_claude = temp_dir / "mock_claude"
    mock_claude.write_text('''#!/bin/bash
echo "Mock claude output: $@"
exit 0
''')
    mock_claude.chmod(0o755)
    
    return {
        "socket_path": str(socket_path),
        "db_path": str(db_path),
        "workdir": str(temp_workdir),
        "mock_claude": str(mock_claude),
    }


class TestPolecatDaemon:
    """Tests for PolecatDaemon class."""

    def test_daemon_creates_socket(self, daemon_setup):
        """Daemon should create Unix socket on start."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        # Start daemon in background
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        assert os.path.exists(daemon_setup["socket_path"])
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_handles_spawn_request(self, daemon_setup):
        """Daemon should handle spawn requests."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        response = client.send({
            "action": "spawn",
            "workdir": daemon_setup["workdir"],
            "task": "test task",
            "allowed_tools": "Read"
        })
        
        assert response["status"] == "ok"
        assert "id" in response
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_handles_status_request(self, daemon_setup):
        """Daemon should handle status requests."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        
        # Spawn a polecat first
        spawn_resp = client.send({
            "action": "spawn",
            "workdir": daemon_setup["workdir"],
            "task": "test",
            "allowed_tools": "Read"
        })
        
        # Get status
        status_resp = client.send({"action": "status"})
        
        assert status_resp["status"] == "ok"
        assert "polecats" in status_resp
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_handles_kill_request(self, daemon_setup, temp_dir: Path):
        """Daemon should handle kill requests."""
        # Create slow mock
        slow_claude = temp_dir / "slow_claude"
        slow_claude.write_text('''#!/bin/bash
sleep 30
exit 0
''')
        slow_claude.chmod(0o755)
        
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=str(slow_claude)
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        
        spawn_resp = client.send({
            "action": "spawn",
            "workdir": daemon_setup["workdir"],
            "task": "test",
            "allowed_tools": "Read"
        })
        polecat_id = spawn_resp["id"]
        
        time.sleep(0.3)  # Let it start
        
        kill_resp = client.send({"action": "kill", "id": polecat_id})
        
        assert kill_resp["status"] == "ok"
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_handles_logs_request(self, daemon_setup):
        """Daemon should handle logs requests."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        
        spawn_resp = client.send({
            "action": "spawn",
            "workdir": daemon_setup["workdir"],
            "task": "test",
            "allowed_tools": "Read"
        })
        polecat_id = spawn_resp["id"]
        
        time.sleep(0.5)  # Wait for completion
        
        logs_resp = client.send({"action": "logs", "id": polecat_id})
        
        assert logs_resp["status"] == "ok"
        assert "content" in logs_resp
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_handles_config_request(self, daemon_setup):
        """Daemon should handle config requests."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        
        response = client.send({
            "action": "config",
            "key": "max_concurrent",
            "value": 8
        })
        
        assert response["status"] == "ok"
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_handles_invalid_request(self, daemon_setup):
        """Daemon should return error for invalid requests."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        
        response = client.send({"action": "nonexistent_action"})
        
        assert response["status"] == "error"
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_graceful_shutdown(self, daemon_setup):
        """Daemon should shut down gracefully."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        daemon.stop()
        thread.join(timeout=2)
        
        assert not thread.is_alive()

    def test_daemon_refuses_second_instance(self, daemon_setup):
        """Second daemon should fail if socket already exists."""
        daemon1 = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread1 = threading.Thread(target=daemon1.start, daemon=True)
        thread1.start()
        time.sleep(0.3)
        
        daemon2 = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        # Second daemon should detect existing socket
        assert daemon2.is_running()
        
        daemon1.stop()
        thread1.join(timeout=2)

    def test_daemon_cleans_stale_socket(self, daemon_setup):
        """Daemon should clean up stale socket files."""
        # Create stale socket file
        socket_path = Path(daemon_setup["socket_path"])
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.touch()
        
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        # Should clean stale socket and start
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        assert daemon._running
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_survives_client_disconnect(self, daemon_setup):
        """Daemon should survive abrupt client disconnects."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        # Connect and immediately disconnect
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(daemon_setup["socket_path"])
        sock.close()
        
        time.sleep(0.2)
        
        # Daemon should still be running
        assert daemon._running
        
        # Should still accept new connections
        client = DaemonClient(daemon_setup["socket_path"])
        response = client.send({"action": "status"})
        assert response["status"] == "ok"
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_handles_rapid_requests(self, daemon_setup):
        """Daemon should handle rapid sequential requests."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        
        for i in range(20):
            response = client.send({"action": "status"})
            assert response["status"] == "ok"
        
        daemon.stop()
        thread.join(timeout=2)

    def test_daemon_status_by_id(self, daemon_setup):
        """Daemon should return status for specific polecat."""
        daemon = PolecatDaemon(
            socket_path=daemon_setup["socket_path"],
            db_path=daemon_setup["db_path"],
            claude_path=daemon_setup["mock_claude"]
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        client = DaemonClient(daemon_setup["socket_path"])
        
        spawn_resp = client.send({
            "action": "spawn",
            "workdir": daemon_setup["workdir"],
            "task": "test",
            "allowed_tools": "Read"
        })
        polecat_id = spawn_resp["id"]
        
        status_resp = client.send({"action": "status", "id": polecat_id})
        
        assert status_resp["status"] == "ok"
        assert "polecat" in status_resp
        assert status_resp["polecat"]["id"] == polecat_id
        
        daemon.stop()
        thread.join(timeout=2)
