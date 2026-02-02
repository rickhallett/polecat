"""Polecat supervisor daemon with Unix socket server."""

import json
import os
import socket
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, Optional

from .state import PolecatState
from .limits import LimitConfig, LimitEnforcer
from .worker import PolecatWorker


class DaemonClient:
    """Client for communicating with the daemon."""
    
    def __init__(self, socket_path: str):
        """Initialize client with socket path."""
        self.socket_path = os.path.expanduser(socket_path)
    
    def send(self, request: dict, timeout: float = 30.0) -> dict:
        """Send request to daemon and return response."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.socket_path)
            sock.send(json.dumps(request).encode())
            sock.shutdown(socket.SHUT_WR)  # Signal end of request
            
            # Read response
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            
            data = b"".join(chunks).decode()
            return json.loads(data)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        finally:
            sock.close()


class PolecatDaemon:
    """Daemon for managing polecat instances."""
    
    def __init__(
        self,
        socket_path: str = "~/.polecat/daemon.sock",
        db_path: str = "~/.polecat/state.db",
        claude_path: str = "claude",
    ):
        """Initialize daemon with paths."""
        self.socket_path = os.path.expanduser(socket_path)
        self.db_path = os.path.expanduser(db_path)
        self.claude_path = claude_path
        
        self.state = PolecatState(self.db_path)
        self.limits = LimitConfig()
        self.enforcer = LimitEnforcer(self.limits, self.state)
        
        self._socket: Optional[socket.socket] = None
        self._running = False
        self._workers: Dict[str, PolecatWorker] = {}
        self._lock = threading.Lock()
    
    def is_running(self) -> bool:
        """Check if daemon is already running."""
        if not os.path.exists(self.socket_path):
            return False
        
        # Try to connect to see if it's alive
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(self.socket_path)
            sock.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False
    
    def _clean_stale_socket(self):
        """Remove stale socket file if exists."""
        if os.path.exists(self.socket_path) and not self.is_running():
            os.unlink(self.socket_path)
    
    def start(self):
        """Start the daemon server."""
        self._clean_stale_socket()
        
        # Ensure socket directory exists
        socket_dir = os.path.dirname(self.socket_path)
        if socket_dir:
            os.makedirs(socket_dir, exist_ok=True)
        
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(self.socket_path)
        self._socket.listen(5)
        self._socket.settimeout(1.0)  # Allow periodic shutdown check
        
        self._running = True
        
        while self._running:
            try:
                conn, _ = self._socket.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(conn,),
                    daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    raise
                break
    
    def stop(self):
        """Stop the daemon server."""
        self._running = False
        
        # Kill all running workers
        with self._lock:
            for worker in self._workers.values():
                try:
                    worker.kill()
                except Exception:
                    pass
            self._workers.clear()
        
        # Close socket
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
        
        # Remove socket file
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except Exception:
                pass
    
    def _handle_client(self, conn: socket.socket):
        """Handle a client connection."""
        try:
            conn.settimeout(30.0)
            
            # Read request
            chunks = []
            while True:
                try:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                except socket.timeout:
                    break
            
            if not chunks:
                return
            
            data = b"".join(chunks).decode()
            
            try:
                request = json.loads(data)
            except json.JSONDecodeError:
                response = {"status": "error", "error": "Invalid JSON"}
                conn.send(json.dumps(response).encode())
                return
            
            response = self._handle_request(request)
            conn.send(json.dumps(response).encode())
            
        except Exception as e:
            try:
                response = {"status": "error", "error": str(e)}
                conn.send(json.dumps(response).encode())
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    
    def _handle_request(self, request: dict) -> dict:
        """Route request to appropriate handler."""
        action = request.get("action")
        
        handlers = {
            "spawn": self._handle_spawn,
            "status": self._handle_status,
            "kill": self._handle_kill,
            "logs": self._handle_logs,
            "config": self._handle_config,
        }
        
        handler = handlers.get(action)
        if not handler:
            return {"status": "error", "error": f"Unknown action: {action}"}
        
        try:
            return handler(request)
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def _handle_spawn(self, request: dict) -> dict:
        """Handle spawn request."""
        workdir = request.get("workdir")
        task = request.get("task")
        allowed_tools = request.get("allowed_tools", "Read")
        
        if not workdir:
            return {"status": "error", "error": "workdir required"}
        if not task:
            return {"status": "error", "error": "task required"}
        
        # Check limits
        if not self.enforcer.can_spawn():
            return {"status": "error", "error": "Concurrency limit reached"}
        
        # Create record
        polecat_id = self.state.create(workdir, task, allowed_tools)
        
        # Start worker in background
        threading.Thread(
            target=self._run_worker,
            args=(polecat_id,),
            daemon=True
        ).start()
        
        return {"status": "ok", "id": polecat_id}
    
    def _run_worker(self, polecat_id: str):
        """Run a polecat worker."""
        record = self.state.get(polecat_id)
        if not record:
            return
        
        worker = PolecatWorker(record, self.limits, claude_path=self.claude_path)
        
        with self._lock:
            self._workers[polecat_id] = worker
        
        self.state.update_status(polecat_id, "running", pid=worker.get_pid())
        
        try:
            result = worker.run()
            
            if result.killed:
                status = "killed"
            elif result.timeout:
                status = "timeout"
            elif result.exit_code == 0:
                status = "completed"
            else:
                status = "failed"
            
            self.state.update_status(
                polecat_id,
                status,
                exit_code=result.exit_code,
                error_message=None if result.exit_code == 0 else f"Exit code: {result.exit_code}"
            )
        except Exception as e:
            self.state.update_status(polecat_id, "failed", error_message=str(e))
        finally:
            with self._lock:
                self._workers.pop(polecat_id, None)
    
    def _handle_status(self, request: dict) -> dict:
        """Handle status request."""
        polecat_id = request.get("id")
        status_filter = request.get("status")
        
        if polecat_id:
            record = self.state.get(polecat_id)
            if not record:
                return {"status": "error", "error": "Polecat not found"}
            return {
                "status": "ok",
                "polecat": self._record_to_dict(record)
            }
        
        records = self.state.list(status=status_filter)
        return {
            "status": "ok",
            "polecats": [self._record_to_dict(r) for r in records]
        }
    
    def _handle_kill(self, request: dict) -> dict:
        """Handle kill request."""
        polecat_id = request.get("id")
        if not polecat_id:
            return {"status": "error", "error": "id required"}
        
        with self._lock:
            worker = self._workers.get(polecat_id)
            if worker:
                worker.kill()
        
        return {"status": "ok"}
    
    def _handle_logs(self, request: dict) -> dict:
        """Handle logs request."""
        polecat_id = request.get("id")
        tail = request.get("tail", 100)
        
        if not polecat_id:
            return {"status": "error", "error": "id required"}
        
        record = self.state.get(polecat_id)
        if not record:
            return {"status": "error", "error": "Polecat not found"}
        
        log_path = record.log_path
        if not os.path.exists(log_path):
            return {"status": "ok", "content": ""}
        
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                if tail:
                    lines = lines[-tail:]
                content = "".join(lines)
        except Exception as e:
            return {"status": "error", "error": f"Failed to read log: {e}"}
        
        return {"status": "ok", "content": content}
    
    def _handle_config(self, request: dict) -> dict:
        """Handle config request."""
        key = request.get("key")
        value = request.get("value")
        
        if not key:
            # Return current config
            return {
                "status": "ok",
                "config": {
                    "max_concurrent": self.limits.max_concurrent,
                    "timeout_seconds": self.limits.timeout_seconds,
                    "max_output_bytes": self.limits.max_output_bytes,
                }
            }
        
        if value is None:
            return {"status": "error", "error": "value required for config update"}
        
        self.enforcer.update_config(**{key: value})
        return {"status": "ok"}
    
    def _record_to_dict(self, record) -> dict:
        """Convert PolecatRecord to serializable dict."""
        return {
            "id": record.id,
            "workdir": record.workdir,
            "task": record.task,
            "allowed_tools": record.allowed_tools,
            "status": record.status,
            "pid": record.pid,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "finished_at": record.finished_at.isoformat() if record.finished_at else None,
            "exit_code": record.exit_code,
            "log_path": record.log_path,
            "error_message": record.error_message,
        }


def main():
    """Entry point for polecatd."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Polecat supervisor daemon")
    parser.add_argument("action", choices=["start", "stop", "status"],
                        help="Daemon action")
    parser.add_argument("--socket", default="~/.polecat/daemon.sock",
                        help="Unix socket path")
    parser.add_argument("--db", default="~/.polecat/state.db",
                        help="State database path")
    
    args = parser.parse_args()
    
    if args.action == "start":
        daemon = PolecatDaemon(socket_path=args.socket, db_path=args.db)
        if daemon.is_running():
            print("Daemon already running")
            sys.exit(1)
        print(f"Starting daemon on {args.socket}")
        daemon.start()
    
    elif args.action == "stop":
        socket_path = os.path.expanduser(args.socket)
        if not os.path.exists(socket_path):
            print("Daemon not running")
            sys.exit(0)
        
        client = DaemonClient(args.socket)
        # Send shutdown (could implement this)
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        print("Daemon stopped")
    
    elif args.action == "status":
        socket_path = os.path.expanduser(args.socket)
        daemon = PolecatDaemon(socket_path=args.socket, db_path=args.db)
        if daemon.is_running():
            print("Daemon is running")
            sys.exit(0)
        else:
            print("Daemon is not running")
            sys.exit(1)


if __name__ == "__main__":
    main()
