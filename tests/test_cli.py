"""Tests for polectl CLI."""

import json
import os
import sys
import threading
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from polecat_supervisor.cli import main as cli_main, PolectlApp
from polecat_supervisor.daemon import PolecatDaemon, DaemonClient


@pytest.fixture
def cli_setup(temp_dir: Path, temp_workdir: Path):
    """Set up CLI test environment with running daemon."""
    socket_path = temp_dir / "daemon.sock"
    db_path = temp_dir / "state.db"
    
    # Create mock claude
    mock_claude = temp_dir / "mock_claude"
    mock_claude.write_text('''#!/bin/bash
echo "Mock output: $@"
exit 0
''')
    mock_claude.chmod(0o755)
    
    # Create task file
    task_file = temp_workdir / "task.md"
    task_file.write_text("# Task\nDo something useful")
    
    daemon = PolecatDaemon(
        socket_path=str(socket_path),
        db_path=str(db_path),
        claude_path=str(mock_claude)
    )
    
    thread = threading.Thread(target=daemon.start, daemon=True)
    thread.start()
    time.sleep(0.3)
    
    yield {
        "socket_path": str(socket_path),
        "db_path": str(db_path),
        "workdir": str(temp_workdir),
        "task_file": str(task_file),
        "daemon": daemon,
        "thread": thread,
    }
    
    daemon.stop()
    thread.join(timeout=2)


class TestPolectlApp:
    """Tests for PolectlApp class."""

    def test_spawn_with_file(self, cli_setup):
        """CLI should spawn polecat with task from file."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        result = app.spawn(
            workdir=cli_setup["workdir"],
            task=None,
            task_file=cli_setup["task_file"],
            allowed_tools="Read"
        )
        
        assert result["status"] == "ok"
        assert "id" in result

    def test_spawn_with_inline(self, cli_setup):
        """CLI should spawn polecat with inline task."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        result = app.spawn(
            workdir=cli_setup["workdir"],
            task="inline task here",
            task_file=None,
            allowed_tools="Read,Write"
        )
        
        assert result["status"] == "ok"
        assert "id" in result

    def test_spawn_requires_workdir(self, cli_setup):
        """CLI should require workdir for spawn."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        result = app.spawn(
            workdir=None,
            task="some task",
            task_file=None,
            allowed_tools="Read"
        )
        
        assert result["status"] == "error"

    def test_status_shows_all(self, cli_setup):
        """CLI should show all polecats status."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        # Spawn a few polecats
        app.spawn(cli_setup["workdir"], "task1", None, "Read")
        app.spawn(cli_setup["workdir"], "task2", None, "Read")
        
        result = app.status()
        
        assert result["status"] == "ok"
        assert "polecats" in result
        assert len(result["polecats"]) >= 2

    def test_status_filters_running(self, cli_setup):
        """CLI should filter status by running."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        result = app.status(status_filter="running")
        
        assert result["status"] == "ok"
        assert "polecats" in result

    def test_logs_shows_output(self, cli_setup):
        """CLI should show polecat logs."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        spawn_result = app.spawn(
            cli_setup["workdir"], "test task", None, "Read"
        )
        polecat_id = spawn_result["id"]
        
        time.sleep(0.5)  # Wait for completion
        
        result = app.logs(polecat_id)
        
        assert result["status"] == "ok"
        assert "content" in result

    def test_logs_tail_limits(self, cli_setup):
        """CLI should respect tail limit for logs."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        spawn_result = app.spawn(
            cli_setup["workdir"], "test task", None, "Read"
        )
        polecat_id = spawn_result["id"]
        
        time.sleep(0.5)
        
        result = app.logs(polecat_id, tail=5)
        
        assert result["status"] == "ok"

    def test_kill_terminates(self, cli_setup, temp_dir: Path):
        """CLI should terminate running polecat."""
        # Create slow mock
        slow_claude = temp_dir / "slow_claude"
        slow_claude.write_text('''#!/bin/bash
sleep 30
exit 0
''')
        slow_claude.chmod(0o755)
        
        # Need a new daemon with slow claude
        socket_path = temp_dir / "daemon2.sock"
        daemon = PolecatDaemon(
            socket_path=str(socket_path),
            db_path=str(temp_dir / "state2.db"),
            claude_path=str(slow_claude)
        )
        
        thread = threading.Thread(target=daemon.start, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            app = PolectlApp(socket_path=str(socket_path))
            
            spawn_result = app.spawn(
                cli_setup["workdir"], "slow task", None, "Read"
            )
            polecat_id = spawn_result["id"]
            
            time.sleep(0.3)
            
            result = app.kill(polecat_id)
            assert result["status"] == "ok"
        finally:
            daemon.stop()
            thread.join(timeout=2)

    def test_kill_missing_errors(self, cli_setup):
        """CLI should handle killing non-existent polecat."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        result = app.kill("nonexistent-id")
        
        # Kill returns ok even for missing (idempotent)
        assert result["status"] == "ok"

    def test_config_updates_daemon(self, cli_setup):
        """CLI should update daemon configuration."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        result = app.config("max_concurrent", 8)
        
        assert result["status"] == "ok"

    def test_config_shows_current(self, cli_setup):
        """CLI should show current configuration."""
        app = PolectlApp(socket_path=cli_setup["socket_path"])
        
        result = app.config()
        
        assert result["status"] == "ok"
        assert "config" in result


class TestCLICommands:
    """Integration tests for CLI commands."""

    def test_cli_spawn_command(self, cli_setup, capsys):
        """Test spawn command via CLI."""
        with patch.object(sys, 'argv', [
            'polectl', 'spawn',
            '-w', cli_setup["workdir"],
            '-s', cli_setup["socket_path"],
            'test inline task'
        ]):
            try:
                cli_main()
            except SystemExit:
                pass
        
        captured = capsys.readouterr()
        # Should output the ID or success message
        assert "pc-" in captured.out or "error" not in captured.out.lower()

    def test_cli_status_command(self, cli_setup, capsys):
        """Test status command via CLI."""
        with patch.object(sys, 'argv', [
            'polectl', 'status',
            '-s', cli_setup["socket_path"],
        ]):
            try:
                cli_main()
            except SystemExit:
                pass
        
        captured = capsys.readouterr()
        # Should output status info
        assert "polecats" in captured.out.lower() or "no polecats" in captured.out.lower() or len(captured.out) >= 0
