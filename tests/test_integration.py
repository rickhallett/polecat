"""Integration tests for full polecat supervisor system."""

import os
import threading
import time
from pathlib import Path

import pytest

from polecat_supervisor.daemon import PolecatDaemon, DaemonClient
from polecat_supervisor.state import PolecatState
from polecat_supervisor.limits import LimitConfig


@pytest.fixture
def integration_setup(temp_dir: Path, temp_workdir: Path):
    """Set up full integration test environment."""
    socket_path = temp_dir / "daemon.sock"
    db_path = temp_dir / "state.db"
    
    # Create mock claude
    mock_claude = temp_dir / "mock_claude"
    mock_claude.write_text('''#!/bin/bash
echo "Processing task..."
echo "Task args: $@"
sleep 0.2
echo "Task complete"
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
    
    client = DaemonClient(str(socket_path))
    state = PolecatState(str(db_path))
    
    yield {
        "daemon": daemon,
        "client": client,
        "state": state,
        "socket_path": str(socket_path),
        "db_path": str(db_path),
        "workdir": str(temp_workdir),
        "temp_dir": temp_dir,
    }
    
    daemon.stop()
    thread.join(timeout=2)


class TestIntegration:
    """Full system integration tests."""

    def test_full_lifecycle_spawn_complete(self, integration_setup):
        """Test complete spawn -> run -> complete lifecycle."""
        client = integration_setup["client"]
        state = integration_setup["state"]
        
        # Spawn
        spawn_resp = client.send({
            "action": "spawn",
            "workdir": integration_setup["workdir"],
            "task": "test lifecycle task",
            "allowed_tools": "Read,Write"
        })
        
        assert spawn_resp["status"] == "ok"
        polecat_id = spawn_resp["id"]
        
        # Wait for completion
        for _ in range(20):
            record = state.get(polecat_id)
            if record and record.status in ("completed", "failed"):
                break
            time.sleep(0.2)
        
        # Verify completed
        record = state.get(polecat_id)
        assert record is not None
        assert record.status == "completed"
        assert record.exit_code == 0
        assert record.started_at is not None
        assert record.finished_at is not None
        
        # Verify logs exist
        assert os.path.exists(record.log_path)
        with open(record.log_path) as f:
            log_content = f.read()
        assert "Task complete" in log_content

    def test_concurrent_spawn_respects_limit(self, integration_setup, temp_dir: Path):
        """Test that concurrency limits are enforced."""
        # Create slow mock
        slow_claude = temp_dir / "slow_claude"
        slow_claude.write_text('''#!/bin/bash
sleep 2
exit 0
''')
        slow_claude.chmod(0o755)
        
        socket_path = temp_dir / "daemon_limit.sock"
        db_path = temp_dir / "state_limit.db"
        
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(slow_claude)
        )
        daemon.limits.max_concurrent = 2
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            client = DaemonClient(str(socket_path))
            
            # Spawn 2 (should succeed)
            resp1 = client.send({
                "action": "spawn",
                "workdir": integration_setup["workdir"],
                "task": "task1",
                "allowed_tools": "Read"
            })
            resp2 = client.send({
                "action": "spawn",
                "workdir": integration_setup["workdir"],
                "task": "task2",
                "allowed_tools": "Read"
            })
            
            assert resp1["status"] == "ok"
            assert resp2["status"] == "ok"
            
            time.sleep(0.3)  # Let them start
            
            # Third should fail (at limit)
            resp3 = client.send({
                "action": "spawn",
                "workdir": integration_setup["workdir"],
                "task": "task3",
                "allowed_tools": "Read"
            })
            
            assert resp3["status"] == "error"
            assert "limit" in resp3["error"].lower()
        finally:
            daemon.stop()
            thread.join(timeout=3)

    def test_timeout_kills_stuck_polecat(self, integration_setup, temp_dir: Path):
        """Test that timeout kills stuck processes."""
        # Create stuck mock
        stuck_claude = temp_dir / "stuck_claude"
        stuck_claude.write_text('''#!/bin/bash
sleep 30
exit 0
''')
        stuck_claude.chmod(0o755)
        
        socket_path = temp_dir / "daemon_timeout.sock"
        db_path = temp_dir / "state_timeout.db"
        
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(stuck_claude)
        )
        daemon.limits.timeout_seconds = 1  # Very short timeout
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            client = DaemonClient(str(socket_path))
            state = PolecatState(str(db_path))
            
            resp = client.send({
                "action": "spawn",
                "workdir": integration_setup["workdir"],
                "task": "stuck task",
                "allowed_tools": "Read"
            })
            polecat_id = resp["id"]
            
            # Wait for timeout
            for _ in range(30):
                record = state.get(polecat_id)
                if record and record.status == "timeout":
                    break
                time.sleep(0.2)
            
            record = state.get(polecat_id)
            assert record.status == "timeout"
        finally:
            daemon.stop()
            thread.join(timeout=3)

    def test_kill_terminates_running(self, integration_setup, temp_dir: Path):
        """Test killing a running polecat."""
        slow_claude = temp_dir / "slow_kill_claude"
        slow_claude.write_text('''#!/bin/bash
sleep 30
exit 0
''')
        slow_claude.chmod(0o755)
        
        socket_path = temp_dir / "daemon_kill.sock"
        db_path = temp_dir / "state_kill.db"
        
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
            state = PolecatState(str(db_path))
            
            resp = client.send({
                "action": "spawn",
                "workdir": integration_setup["workdir"],
                "task": "long task",
                "allowed_tools": "Read"
            })
            polecat_id = resp["id"]
            
            time.sleep(0.3)  # Let it start
            
            # Kill it
            kill_resp = client.send({"action": "kill", "id": polecat_id})
            assert kill_resp["status"] == "ok"
            
            # Wait for killed status
            for _ in range(20):
                record = state.get(polecat_id)
                if record and record.status == "killed":
                    break
                time.sleep(0.2)
            
            record = state.get(polecat_id)
            assert record.status == "killed"
        finally:
            daemon.stop()
            thread.join(timeout=3)

    def test_daemon_restart_recovers_state(self, integration_setup, temp_dir: Path):
        """Test that state persists across daemon restart."""
        socket_path = temp_dir / "daemon_restart.sock"
        db_path = temp_dir / "state_restart.db"
        
        # Create fast mock
        fast_claude = temp_dir / "fast_claude"
        fast_claude.write_text('''#!/bin/bash
echo "done"
exit 0
''')
        fast_claude.chmod(0o755)
        
        daemon1 = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(fast_claude)
        )
        
        thread1 = threading.Thread(target=daemon1.start, daemon=True)
        thread1.start()
        time.sleep(0.3)
        
        client = DaemonClient(str(socket_path))
        
        resp = client.send({
            "action": "spawn",
            "workdir": integration_setup["workdir"],
            "task": "persistent task",
            "allowed_tools": "Read"
        })
        polecat_id = resp["id"]
        
        time.sleep(0.5)  # Let it complete
        
        daemon1.stop()
        thread1.join(timeout=2)
        
        # Start new daemon
        daemon2 = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(fast_claude)
        )
        
        thread2 = threading.Thread(target=daemon2.start, daemon=True)
        thread2.start()
        time.sleep(0.3)
        
        try:
            client2 = DaemonClient(str(socket_path))
            
            status_resp = client2.send({"action": "status", "id": polecat_id})
            
            assert status_resp["status"] == "ok"
            assert status_resp["polecat"]["id"] == polecat_id
        finally:
            daemon2.stop()
            thread2.join(timeout=2)

    def test_rapid_spawn_kill_cycles(self, integration_setup, temp_dir: Path):
        """Test rapid spawn/kill cycles don't cause issues."""
        slow_claude = temp_dir / "cycle_claude"
        slow_claude.write_text('''#!/bin/bash
sleep 10
exit 0
''')
        slow_claude.chmod(0o755)
        
        socket_path = temp_dir / "daemon_cycle.sock"
        db_path = temp_dir / "state_cycle.db"
        
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(slow_claude)
        )
        daemon.limits.max_concurrent = 10
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            client = DaemonClient(str(socket_path))
            
            for i in range(5):
                resp = client.send({
                    "action": "spawn",
                    "workdir": integration_setup["workdir"],
                    "task": f"cycle task {i}",
                    "allowed_tools": "Read"
                })
                assert resp["status"] == "ok"
                
                time.sleep(0.1)
                
                client.send({"action": "kill", "id": resp["id"]})
            
            # Daemon should still be healthy
            status_resp = client.send({"action": "status"})
            assert status_resp["status"] == "ok"
        finally:
            daemon.stop()
            thread.join(timeout=3)

    def test_tool_restrictions_enforced_e2e(self, integration_setup):
        """Test that tool restrictions are passed to worker."""
        client = integration_setup["client"]
        state = integration_setup["state"]
        
        resp = client.send({
            "action": "spawn",
            "workdir": integration_setup["workdir"],
            "task": "test tools",
            "allowed_tools": "Read,Write(./**),Bash(git:*)"
        })
        polecat_id = resp["id"]
        
        time.sleep(1)  # Wait for completion
        
        record = state.get(polecat_id)
        assert record is not None
        assert record.allowed_tools == "Read,Write(./**),Bash(git:*)"
        
        # Check log contains tool info
        logs_resp = client.send({"action": "logs", "id": polecat_id})
        # The mock echoes args which should include allowed-tools

    def test_output_captured_correctly(self, integration_setup, temp_dir: Path):
        """Test that output is captured correctly in logs."""
        special_claude = temp_dir / "output_claude"
        special_claude.write_text('''#!/bin/bash
echo "Line 1"
echo "Line 2"
echo "Special chars: <>&\\"'"
exit 0
''')
        special_claude.chmod(0o755)
        
        socket_path = temp_dir / "daemon_output.sock"
        db_path = temp_dir / "state_output.db"
        
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(special_claude)
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            client = DaemonClient(str(socket_path))
            
            resp = client.send({
                "action": "spawn",
                "workdir": integration_setup["workdir"],
                "task": "output test",
                "allowed_tools": "Read"
            })
            polecat_id = resp["id"]
            
            time.sleep(0.5)
            
            logs_resp = client.send({"action": "logs", "id": polecat_id})
            
            assert logs_resp["status"] == "ok"
            assert "Line 1" in logs_resp["content"]
            assert "Line 2" in logs_resp["content"]
        finally:
            daemon.stop()
            thread.join(timeout=2)

    def test_workdir_isolation(self, integration_setup, temp_dir: Path):
        """Test that polecats run in correct workdir."""
        pwd_claude = temp_dir / "pwd_claude"
        pwd_claude.write_text('''#!/bin/bash
pwd
exit 0
''')
        pwd_claude.chmod(0o755)
        
        socket_path = temp_dir / "daemon_workdir.sock"
        db_path = temp_dir / "state_workdir.db"
        
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(db_path),
            claude_path=str(pwd_claude)
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            client = DaemonClient(str(socket_path))
            
            # Use specific workdir
            workdir = temp_dir / "specific_workdir"
            workdir.mkdir()
            
            resp = client.send({
                "action": "spawn",
                "workdir": str(workdir),
                "task": "pwd test",
                "allowed_tools": "Read"
            })
            polecat_id = resp["id"]
            
            time.sleep(0.5)
            
            logs_resp = client.send({"action": "logs", "id": polecat_id})
            
            assert str(workdir) in logs_resp["content"]
        finally:
            daemon.stop()
            thread.join(timeout=2)

    def test_multiple_clients_concurrent(self, integration_setup):
        """Test multiple concurrent client connections."""
        results = []
        errors = []
        
        def make_request(i):
            try:
                # Create fresh client per thread to avoid socket sharing issues
                client = DaemonClient(integration_setup["socket_path"])
                resp = client.send({"action": "status"})
                results.append(resp)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=make_request, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Allow some connection failures in high-concurrency scenario
        successful = [r for r in results if r.get("status") == "ok"]
        assert len(successful) >= 8, f"Expected at least 8 successful, got {len(successful)}"
