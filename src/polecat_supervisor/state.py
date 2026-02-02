"""SQLite-backed state management for polecat instances."""

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Any


@dataclass
class PolecatRecord:
    """Represents a polecat instance in the state database."""
    id: str
    workdir: str
    task: str
    allowed_tools: str
    status: str  # pending, running, completed, failed, killed, timeout
    pid: Optional[int]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    exit_code: Optional[int]
    log_path: str
    error_message: Optional[str]


class PolecatState:
    """Manages polecat state in SQLite database."""
    
    def __init__(self, db_path: str = "~/.polecat/state.db"):
        """Initialize state manager with database path."""
        self.db_path = os.path.expanduser(db_path)
        self._ensure_db_directory()
        self._init_db()
    
    def _ensure_db_directory(self):
        """Create database directory if it doesn't exist."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS polecats (
                id TEXT PRIMARY KEY,
                workdir TEXT NOT NULL,
                task TEXT NOT NULL,
                allowed_tools TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                pid INTEGER,
                started_at TEXT,
                finished_at TEXT,
                exit_code INTEGER,
                log_path TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON polecats(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON polecats(created_at)")
        conn.commit()
        conn.close()
    
    def _generate_id(self) -> str:
        """Generate a unique polecat ID."""
        return f"pc-{uuid.uuid4().hex[:12]}"
    
    def _generate_log_path(self, polecat_id: str) -> str:
        """Generate log file path for a polecat."""
        log_dir = os.path.expanduser("~/.polecat/logs")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"{polecat_id}.log")
    
    def _row_to_record(self, row: sqlite3.Row) -> PolecatRecord:
        """Convert database row to PolecatRecord."""
        started_at = None
        if row["started_at"]:
            started_at = datetime.fromisoformat(row["started_at"])
        
        finished_at = None
        if row["finished_at"]:
            finished_at = datetime.fromisoformat(row["finished_at"])
        
        return PolecatRecord(
            id=row["id"],
            workdir=row["workdir"],
            task=row["task"],
            allowed_tools=row["allowed_tools"],
            status=row["status"],
            pid=row["pid"],
            started_at=started_at,
            finished_at=finished_at,
            exit_code=row["exit_code"],
            log_path=row["log_path"],
            error_message=row["error_message"],
        )
    
    def create(self, workdir: str, task: str, allowed_tools: str) -> str:
        """Create a new polecat record and return its ID."""
        polecat_id = self._generate_id()
        log_path = self._generate_log_path(polecat_id)
        created_at = datetime.now().isoformat()
        
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO polecats (id, workdir, task, allowed_tools, status, log_path, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (polecat_id, workdir, task, allowed_tools, log_path, created_at))
        conn.commit()
        conn.close()
        
        return polecat_id
    
    def get(self, polecat_id: str) -> Optional[PolecatRecord]:
        """Get a polecat record by ID, or None if not found."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM polecats WHERE id = ?", (polecat_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row is None:
            return None
        return self._row_to_record(row)
    
    def list(self, status: Optional[str] = None) -> List[PolecatRecord]:
        """List polecat records, optionally filtered by status."""
        conn = self._get_connection()
        
        if status:
            cursor = conn.execute(
                "SELECT * FROM polecats WHERE status = ? ORDER BY created_at DESC",
                (status,)
            )
        else:
            cursor = conn.execute("SELECT * FROM polecats ORDER BY created_at DESC")
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_record(row) for row in rows]
    
    def update_status(self, polecat_id: str, status: str, **kwargs: Any):
        """Update the status and optional fields of a polecat."""
        conn = self._get_connection()
        
        # Build update query dynamically based on status and kwargs
        updates = ["status = ?"]
        params: List[Any] = [status]
        
        # Auto-set timestamps based on status
        if status == "running":
            updates.append("started_at = ?")
            params.append(datetime.now().isoformat())
        elif status in ("completed", "failed", "killed", "timeout"):
            updates.append("finished_at = ?")
            params.append(datetime.now().isoformat())
        
        # Handle additional kwargs
        allowed_fields = {"pid", "exit_code", "error_message"}
        for key, value in kwargs.items():
            if key in allowed_fields:
                updates.append(f"{key} = ?")
                params.append(value)
        
        params.append(polecat_id)
        query = f"UPDATE polecats SET {', '.join(updates)} WHERE id = ?"
        
        conn.execute(query, params)
        conn.commit()
        conn.close()
    
    def cleanup_old(self, days: int = 7):
        """Remove completed/failed polecats older than specified days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        
        conn = self._get_connection()
        conn.execute("""
            DELETE FROM polecats 
            WHERE status IN ('completed', 'failed', 'killed', 'timeout')
            AND finished_at < ?
        """, (cutoff,))
        conn.commit()
        conn.close()
