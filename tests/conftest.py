"""Shared test fixtures for polecat supervisor tests."""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test isolation."""
    td = tempfile.mkdtemp(prefix="polecat_test_")
    yield Path(td)
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def temp_db(temp_dir: Path) -> Path:
    """Return path for a temporary SQLite database."""
    return temp_dir / "state.db"


@pytest.fixture
def temp_socket(temp_dir: Path) -> Path:
    """Return path for a temporary Unix socket."""
    return temp_dir / "daemon.sock"


@pytest.fixture
def temp_workdir(temp_dir: Path) -> Path:
    """Create a temporary working directory with basic structure."""
    workdir = temp_dir / "workdir"
    workdir.mkdir()
    (workdir / "test.txt").write_text("test content")
    return workdir


@pytest.fixture
def sample_task() -> str:
    """Return a simple task string for testing."""
    return "List the files in this directory"


@pytest.fixture
def sample_allowed_tools() -> str:
    """Return default allowed tools string."""
    return "Read,Write(./**)"


@pytest.fixture
def mock_claude_path(temp_dir: Path) -> Path:
    """Create a mock claude executable for testing."""
    mock_claude = temp_dir / "mock_claude"
    mock_claude.write_text('''#!/bin/bash
# Mock claude for testing
echo "Mock claude output"
echo "Task: $*"
exit 0
''')
    mock_claude.chmod(0o755)
    return mock_claude


@pytest.fixture
def mock_claude_slow(temp_dir: Path) -> Path:
    """Create a slow mock claude for timeout testing."""
    mock_claude = temp_dir / "mock_claude_slow"
    mock_claude.write_text('''#!/bin/bash
# Slow mock claude for timeout testing
sleep 30
echo "This should not appear"
exit 0
''')
    mock_claude.chmod(0o755)
    return mock_claude


@pytest.fixture
def mock_claude_fail(temp_dir: Path) -> Path:
    """Create a failing mock claude for error testing."""
    mock_claude = temp_dir / "mock_claude_fail"
    mock_claude.write_text('''#!/bin/bash
# Failing mock claude
echo "Error occurred" >&2
exit 1
''')
    mock_claude.chmod(0o755)
    return mock_claude
