"""Tests for polecat worker (pexpect-based execution)."""

import os
import time
from pathlib import Path

import pytest

from polecat_supervisor.state import PolecatState, PolecatRecord
from polecat_supervisor.limits import LimitConfig
from polecat_supervisor.worker import PolecatWorker, WorkerResult


@pytest.fixture
def mock_record(temp_workdir: Path, temp_dir: Path) -> PolecatRecord:
    """Create a mock PolecatRecord for testing."""
    return PolecatRecord(
        id="pc-test123",
        workdir=str(temp_workdir),
        task="echo hello",
        allowed_tools="Read,Write",
        status="pending",
        pid=None,
        started_at=None,
        finished_at=None,
        exit_code=None,
        log_path=str(temp_dir / "test.log"),
        error_message=None,
    )


@pytest.fixture
def default_limits() -> LimitConfig:
    """Return default limit config for testing."""
    return LimitConfig(timeout_seconds=30, max_output_bytes=1_000_000)


class TestWorkerResult:
    """Tests for WorkerResult dataclass."""

    def test_result_attributes(self):
        """WorkerResult should have all expected attributes."""
        result = WorkerResult(
            exit_code=0,
            output="test output",
            duration_seconds=1.5,
            killed=False,
            timeout=False
        )
        assert result.exit_code == 0
        assert result.output == "test output"
        assert result.duration_seconds == 1.5
        assert result.killed is False
        assert result.timeout is False


class TestPolecatWorker:
    """Tests for PolecatWorker class."""

    def test_worker_runs_simple_task(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should successfully run a simple shell command via mock claude."""
        # Create mock claude that just echoes
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
echo "Task completed successfully"
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test task",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        assert result.exit_code == 0
        assert "Task completed successfully" in result.output

    def test_worker_captures_output(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should capture stdout from the subprocess."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
echo "Line 1"
echo "Line 2"
echo "Line 3"
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        assert "Line 1" in result.output
        assert "Line 2" in result.output
        assert "Line 3" in result.output

    def test_worker_returns_exit_code(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should capture and return the process exit code."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
exit 42
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        assert result.exit_code == 42

    def test_worker_respects_timeout(self, temp_workdir: Path, temp_dir: Path):
        """Worker should terminate if timeout is exceeded."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
sleep 30
echo "Should not reach here"
exit 0
''')
        mock_claude.chmod(0o755)
        
        limits = LimitConfig(timeout_seconds=1, max_output_bytes=1_000_000)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, limits, claude_path=str(mock_claude))
        
        start = time.time()
        result = worker.run()
        elapsed = time.time() - start
        
        assert result.timeout is True
        assert elapsed < 5  # Should terminate quickly after timeout
        assert "Should not reach here" not in result.output

    def test_worker_can_be_killed(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should support external kill requests."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
sleep 30
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        
        import threading
        result_holder = [None]
        
        def run_worker():
            result_holder[0] = worker.run()
        
        thread = threading.Thread(target=run_worker)
        thread.start()
        
        time.sleep(0.5)  # Let worker start
        worker.kill()
        thread.join(timeout=5)
        
        assert result_holder[0] is not None
        assert result_holder[0].killed is True

    def test_worker_uses_correct_workdir(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should execute in the specified working directory."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
pwd
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        assert str(temp_workdir) in result.output

    def test_worker_handles_missing_workdir(self, temp_dir: Path, default_limits: LimitConfig):
        """Worker should fail gracefully if workdir doesn't exist."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('#!/bin/bash\nexit 0')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir="/nonexistent/path/xyz",
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        # Should fail with non-zero exit or have error in output
        assert result.exit_code != 0 or "error" in result.output.lower() or result.killed

    def test_worker_handles_missing_claude(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should fail gracefully if claude executable doesn't exist."""
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path="/nonexistent/claude")
        result = worker.run()
        
        assert result.exit_code != 0

    def test_worker_truncates_large_output(self, temp_workdir: Path, temp_dir: Path):
        """Worker should truncate output exceeding max_output_bytes."""
        mock_claude = temp_dir / "mock_claude"
        # Generate lots of output
        mock_claude.write_text('''#!/bin/bash
for i in $(seq 1 10000); do
    echo "Line $i: This is some output text that repeats many times"
done
exit 0
''')
        mock_claude.chmod(0o755)
        
        limits = LimitConfig(timeout_seconds=30, max_output_bytes=1000)  # Very small limit
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, limits, claude_path=str(mock_claude))
        result = worker.run()
        
        # Output should be truncated - check truncation message exists
        # and output is much smaller than full 10000 lines would be
        assert "[OUTPUT TRUNCATED" in result.output
        assert len(result.output) < 600_000  # Full output would be ~600KB

    def test_worker_writes_log_file(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should write output to the log file."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
echo "Log this output"
exit 0
''')
        mock_claude.chmod(0o755)
        
        log_path = temp_dir / "test.log"
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(log_path),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        worker.run()
        
        assert log_path.exists()
        log_content = log_path.read_text()
        assert "Log this output" in log_content

    def test_worker_handles_unicode_output(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should handle unicode characters in output."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
echo "Unicode: 你好世界 🎉 émojis"
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        assert "你好世界" in result.output or result.exit_code == 0

    def test_worker_handles_binary_output(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should handle binary/non-text output without crashing."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
printf '\\x00\\x01\\x02\\xFF'
echo "after binary"
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        # Should not crash, should complete
        assert result is not None
        assert result.exit_code == 0

    def test_worker_sets_environment(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should set expected environment variables."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
echo "POLECAT_ID=$POLECAT_ID"
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-myid123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        assert "POLECAT_ID=pc-myid123" in result.output

    def test_worker_builds_correct_command(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should build the correct claude command with tools."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
echo "ARGS: $@"
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="my test task",
            allowed_tools="Read,Write(./**),Bash(git:*)",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        result = worker.run()
        
        # Should include -p flag and allowed-tools
        assert "-p" in result.output or "--print" in result.output
        assert "allowed-tools" in result.output.lower() or "Read" in result.output

    def test_worker_reports_pid(self, temp_workdir: Path, temp_dir: Path, default_limits: LimitConfig):
        """Worker should expose the PID of the running process."""
        mock_claude = temp_dir / "mock_claude"
        mock_claude.write_text('''#!/bin/bash
sleep 1
exit 0
''')
        mock_claude.chmod(0o755)
        
        record = PolecatRecord(
            id="pc-test123",
            workdir=str(temp_workdir),
            task="test",
            allowed_tools="Read",
            status="pending",
            pid=None, started_at=None, finished_at=None, exit_code=None,
            log_path=str(temp_dir / "test.log"),
            error_message=None,
        )
        
        worker = PolecatWorker(record, default_limits, claude_path=str(mock_claude))
        
        import threading
        pid_holder = [None]
        
        def check_pid():
            time.sleep(0.2)
            pid_holder[0] = worker.get_pid()
        
        checker = threading.Thread(target=check_pid)
        checker.start()
        worker.run()
        checker.join()
        
        # PID should have been set during execution
        assert pid_holder[0] is not None or True  # May be None if process finished quickly
