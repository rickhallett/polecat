"""Polecat worker - executes sandboxed Claude CLI with pexpect."""

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pexpect

from .state import PolecatRecord
from .limits import LimitConfig


@dataclass
class WorkerResult:
    """Result of a polecat worker execution."""
    exit_code: int
    output: str
    duration_seconds: float
    killed: bool
    timeout: bool


class PolecatWorker:
    """Executes a polecat task using pexpect for PTY management."""
    
    def __init__(
        self,
        record: PolecatRecord,
        limits: LimitConfig,
        claude_path: str = "claude"
    ):
        """Initialize worker with record and limits."""
        self.record = record
        self.limits = limits
        self.claude_path = claude_path
        self._child: Optional[pexpect.spawn] = None
        self._killed = False
        self._pid: Optional[int] = None
    
    def _build_command(self) -> str:
        """Build the claude command string."""
        # Escape task for shell
        task_escaped = self.record.task.replace("'", "'\\''")
        
        cmd_parts = [
            self.claude_path,
            "-p",  # Print mode (non-interactive)
            f"--allowedTools '{self.record.allowed_tools}'",
            "--",
            f"'{task_escaped}'"
        ]
        
        return " ".join(cmd_parts)
    
    def _setup_environment(self) -> dict:
        """Set up environment variables for the subprocess."""
        env = os.environ.copy()
        env["POLECAT_ID"] = self.record.id
        return env
    
    def get_pid(self) -> Optional[int]:
        """Return the PID of the running process, if any."""
        return self._pid
    
    def run(self) -> WorkerResult:
        """Execute the polecat task and return the result."""
        start_time = time.time()
        output_chunks = []
        total_bytes = 0
        exit_code = -1
        timeout_occurred = False
        
        # Check workdir exists
        workdir = Path(self.record.workdir)
        if not workdir.exists():
            return WorkerResult(
                exit_code=1,
                output=f"Error: Working directory does not exist: {self.record.workdir}",
                duration_seconds=time.time() - start_time,
                killed=False,
                timeout=False
            )
        
        # Check claude exists
        if not Path(self.claude_path).exists() and not self._command_exists(self.claude_path):
            return WorkerResult(
                exit_code=127,
                output=f"Error: Claude executable not found: {self.claude_path}",
                duration_seconds=time.time() - start_time,
                killed=False,
                timeout=False
            )
        
        command = self._build_command()
        env = self._setup_environment()
        
        try:
            self._child = pexpect.spawn(
                "/bin/bash",
                ["-c", command],
                cwd=str(workdir),
                timeout=self.limits.timeout_seconds,
                encoding="utf-8",
                env=env,
                codec_errors="replace"  # Handle binary/invalid UTF-8
            )
            self._pid = self._child.pid
            
            # Read output in chunks
            while True:
                if self._killed:
                    break
                
                try:
                    # Read with small timeout for responsiveness
                    chunk = self._child.read_nonblocking(size=4096, timeout=0.1)
                    if chunk:
                        output_chunks.append(chunk)
                        total_bytes += len(chunk.encode("utf-8", errors="replace"))
                        
                        # Check output size limit
                        if total_bytes > self.limits.max_output_bytes:
                            output_chunks.append("\n[OUTPUT TRUNCATED - size limit exceeded]")
                            self._child.terminate(force=True)
                            break
                            
                except pexpect.TIMEOUT:
                    # Check if process is still alive
                    if not self._child.isalive():
                        break
                    # Check overall timeout
                    elapsed = time.time() - start_time
                    if elapsed > self.limits.timeout_seconds:
                        timeout_occurred = True
                        self._child.terminate(force=True)
                        break
                except pexpect.EOF:
                    break
            
            # Wait for process to finish
            if self._child.isalive():
                try:
                    self._child.expect(pexpect.EOF, timeout=5)
                except (pexpect.TIMEOUT, pexpect.EOF):
                    self._child.terminate(force=True)
            
            self._child.close()
            exit_code = self._child.exitstatus if self._child.exitstatus is not None else -1
            
        except pexpect.ExceptionPexpect as e:
            output_chunks.append(f"\nError: {str(e)}")
            exit_code = 1
        except Exception as e:
            output_chunks.append(f"\nUnexpected error: {str(e)}")
            exit_code = 1
        finally:
            self._child = None
            self._pid = None
        
        duration = time.time() - start_time
        output = "".join(output_chunks)
        
        # Write to log file
        self._write_log(output, exit_code, duration)
        
        return WorkerResult(
            exit_code=exit_code,
            output=output,
            duration_seconds=duration,
            killed=self._killed,
            timeout=timeout_occurred
        )
    
    def _command_exists(self, cmd: str) -> bool:
        """Check if a command exists in PATH."""
        import shutil
        return shutil.which(cmd) is not None
    
    def _write_log(self, output: str, exit_code: int, duration: float):
        """Write execution log to file."""
        try:
            log_dir = Path(self.record.log_path).parent
            log_dir.mkdir(parents=True, exist_ok=True)
            
            with open(self.record.log_path, "w", encoding="utf-8") as f:
                f.write(f"=== Polecat {self.record.id} ===\n")
                f.write(f"Task: {self.record.task}\n")
                f.write(f"Workdir: {self.record.workdir}\n")
                f.write(f"Allowed tools: {self.record.allowed_tools}\n")
                f.write(f"Duration: {duration:.2f}s\n")
                f.write(f"Exit code: {exit_code}\n")
                f.write(f"Killed: {self._killed}\n")
                f.write("=" * 40 + "\n\n")
                f.write(output)
        except Exception:
            pass  # Log writing is best-effort
    
    def kill(self):
        """Terminate the running process."""
        self._killed = True
        if self._child and self._child.isalive():
            try:
                self._child.terminate(force=True)
            except Exception:
                pass
