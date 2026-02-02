"""Tests for blast radius controls (concurrency, timeouts)."""

import threading
import time
from pathlib import Path

import pytest

from polecat_supervisor.state import PolecatState
from polecat_supervisor.limits import LimitConfig, LimitEnforcer


class TestLimitConfig:
    """Tests for LimitConfig dataclass."""

    def test_default_values(self):
        """LimitConfig should have sensible defaults."""
        config = LimitConfig()
        assert config.max_concurrent == 4
        assert config.timeout_seconds == 600
        assert config.max_output_bytes == 10_000_000

    def test_custom_values(self):
        """LimitConfig should accept custom values."""
        config = LimitConfig(max_concurrent=8, timeout_seconds=300, max_output_bytes=5_000_000)
        assert config.max_concurrent == 8
        assert config.timeout_seconds == 300
        assert config.max_output_bytes == 5_000_000


class TestLimitEnforcer:
    """Tests for LimitEnforcer class."""

    def test_can_spawn_true_when_under_limit(self, temp_db: Path, temp_workdir: Path):
        """can_spawn should return True when under concurrency limit."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=4)
        enforcer = LimitEnforcer(config, state)
        
        assert enforcer.can_spawn() is True

    def test_can_spawn_false_at_limit(self, temp_db: Path, temp_workdir: Path):
        """can_spawn should return False when at concurrency limit."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=2)
        enforcer = LimitEnforcer(config, state)
        
        # Create running polecats up to limit
        id1 = state.create(str(temp_workdir), "task1", "Read")
        id2 = state.create(str(temp_workdir), "task2", "Read")
        state.update_status(id1, "running")
        state.update_status(id2, "running")
        
        assert enforcer.can_spawn() is False

    def test_can_spawn_true_after_completion(self, temp_db: Path, temp_workdir: Path):
        """can_spawn should return True after a running polecat completes."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=1)
        enforcer = LimitEnforcer(config, state)
        
        # Create and complete a polecat
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        state.update_status(polecat_id, "running")
        assert enforcer.can_spawn() is False
        
        state.update_status(polecat_id, "completed")
        assert enforcer.can_spawn() is True

    def test_wait_for_slot_returns_immediately_if_available(self, temp_db: Path):
        """wait_for_slot should return immediately if under limit."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=4)
        enforcer = LimitEnforcer(config, state)
        
        start = time.time()
        result = enforcer.wait_for_slot(timeout=5.0)
        elapsed = time.time() - start
        
        assert result is True
        assert elapsed < 0.5  # Should be nearly instant

    def test_wait_for_slot_blocks_until_available(self, temp_db: Path, temp_workdir: Path):
        """wait_for_slot should block until a slot becomes available."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=1)
        enforcer = LimitEnforcer(config, state)
        
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        state.update_status(polecat_id, "running")
        
        result_holder = [None]
        
        def complete_polecat():
            time.sleep(0.3)
            state.update_status(polecat_id, "completed")
        
        def wait_for_slot():
            result_holder[0] = enforcer.wait_for_slot(timeout=2.0)
        
        completer = threading.Thread(target=complete_polecat)
        waiter = threading.Thread(target=wait_for_slot)
        
        completer.start()
        waiter.start()
        completer.join()
        waiter.join()
        
        assert result_holder[0] is True

    def test_wait_for_slot_timeout(self, temp_db: Path, temp_workdir: Path):
        """wait_for_slot should return False on timeout."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=1)
        enforcer = LimitEnforcer(config, state)
        
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        state.update_status(polecat_id, "running")
        
        start = time.time()
        result = enforcer.wait_for_slot(timeout=0.5)
        elapsed = time.time() - start
        
        assert result is False
        assert elapsed >= 0.4  # Should have waited close to timeout

    def test_active_count_accurate(self, temp_db: Path, temp_workdir: Path):
        """active_count should accurately reflect running polecats."""
        state = PolecatState(str(temp_db))
        config = LimitConfig()
        enforcer = LimitEnforcer(config, state)
        
        assert enforcer.active_count() == 0
        
        id1 = state.create(str(temp_workdir), "task1", "Read")
        state.update_status(id1, "running")
        assert enforcer.active_count() == 1
        
        id2 = state.create(str(temp_workdir), "task2", "Read")
        state.update_status(id2, "running")
        assert enforcer.active_count() == 2
        
        state.update_status(id1, "completed")
        assert enforcer.active_count() == 1

    def test_config_update_takes_effect(self, temp_db: Path, temp_workdir: Path):
        """Updating config should affect limit enforcement."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=1)
        enforcer = LimitEnforcer(config, state)
        
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        state.update_status(polecat_id, "running")
        
        assert enforcer.can_spawn() is False
        
        # Update config to allow more
        enforcer.update_config(max_concurrent=2)
        assert enforcer.can_spawn() is True

    def test_concurrent_can_spawn_checks(self, temp_db: Path, temp_workdir: Path):
        """Concurrent can_spawn checks should be thread-safe."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=2)
        enforcer = LimitEnforcer(config, state)
        
        # Create one running polecat
        polecat_id = state.create(str(temp_workdir), "task", "Read")
        state.update_status(polecat_id, "running")
        
        results = []
        
        def check_spawn():
            results.append(enforcer.can_spawn())
        
        threads = [threading.Thread(target=check_spawn) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All should return True since we're under limit
        assert all(results)

    def test_limit_zero_blocks_all(self, temp_db: Path):
        """max_concurrent=0 should block all spawns."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=0)
        enforcer = LimitEnforcer(config, state)
        
        assert enforcer.can_spawn() is False

    def test_pending_not_counted_as_active(self, temp_db: Path, temp_workdir: Path):
        """Pending polecats should not count toward active limit."""
        state = PolecatState(str(temp_db))
        config = LimitConfig(max_concurrent=1)
        enforcer = LimitEnforcer(config, state)
        
        # Create pending polecat
        state.create(str(temp_workdir), "task", "Read")
        
        # Should still be able to spawn
        assert enforcer.can_spawn() is True
        assert enforcer.active_count() == 0
