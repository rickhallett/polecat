"""Blast radius controls for polecat concurrency and resources."""

import time
import threading
from dataclasses import dataclass, field
from typing import Any

from .state import PolecatState


@dataclass
class LimitConfig:
    """Configuration for polecat resource limits."""
    max_concurrent: int = 4
    timeout_seconds: int = 600
    max_output_bytes: int = 10_000_000  # 10MB


class LimitEnforcer:
    """Enforces resource limits on polecat spawning."""
    
    def __init__(self, config: LimitConfig, state: PolecatState):
        """Initialize with config and state manager."""
        self.config = config
        self.state = state
        self._lock = threading.Lock()
    
    def active_count(self) -> int:
        """Return the number of currently running polecats."""
        running = self.state.list(status="running")
        return len(running)
    
    def can_spawn(self) -> bool:
        """Check if spawning a new polecat is allowed under current limits."""
        with self._lock:
            if self.config.max_concurrent <= 0:
                return False
            return self.active_count() < self.config.max_concurrent
    
    def wait_for_slot(self, timeout: float = 60.0) -> bool:
        """
        Wait until a spawn slot becomes available.
        
        Returns True if a slot became available, False on timeout.
        """
        deadline = time.time() + timeout
        poll_interval = 0.1
        
        while time.time() < deadline:
            if self.can_spawn():
                return True
            time.sleep(poll_interval)
        
        return False
    
    def update_config(self, **kwargs: Any):
        """Update configuration values."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
