"""polectl - CLI for polecat supervisor daemon."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .daemon import DaemonClient


class PolectlApp:
    """Application logic for polectl CLI."""
    
    def __init__(self, socket_path: str = "~/.polecat/daemon.sock"):
        """Initialize with daemon socket path."""
        self.socket_path = os.path.expanduser(socket_path)
        self.client = DaemonClient(self.socket_path)
    
    def spawn(
        self,
        workdir: Optional[str],
        task: Optional[str],
        task_file: Optional[str],
        allowed_tools: str = "Read"
    ) -> dict:
        """Spawn a new polecat."""
        if not workdir:
            return {"status": "error", "error": "workdir is required"}
        
        # Get task content
        if task_file:
            try:
                with open(task_file, "r", encoding="utf-8") as f:
                    task_content = f.read()
            except Exception as e:
                return {"status": "error", "error": f"Failed to read task file: {e}"}
        elif task:
            task_content = task
        else:
            return {"status": "error", "error": "task or task_file required"}
        
        return self.client.send({
            "action": "spawn",
            "workdir": os.path.expanduser(workdir),
            "task": task_content,
            "allowed_tools": allowed_tools,
        })
    
    def status(self, polecat_id: Optional[str] = None, status_filter: Optional[str] = None) -> dict:
        """Get status of polecats."""
        request = {"action": "status"}
        if polecat_id:
            request["id"] = polecat_id
        if status_filter:
            request["status"] = status_filter
        return self.client.send(request)
    
    def logs(self, polecat_id: str, tail: int = 100) -> dict:
        """Get logs for a polecat."""
        return self.client.send({
            "action": "logs",
            "id": polecat_id,
            "tail": tail,
        })
    
    def kill(self, polecat_id: str) -> dict:
        """Kill a running polecat."""
        return self.client.send({
            "action": "kill",
            "id": polecat_id,
        })
    
    def config(self, key: Optional[str] = None, value: Optional[int] = None) -> dict:
        """Get or set daemon configuration."""
        request = {"action": "config"}
        if key:
            request["key"] = key
        if value is not None:
            request["value"] = value
        return self.client.send(request)


def format_status(result: dict) -> str:
    """Format status result for display."""
    if result["status"] != "ok":
        return f"Error: {result.get('error', 'Unknown error')}"
    
    lines = []
    
    if "polecat" in result:
        p = result["polecat"]
        lines.append(format_polecat(p))
    elif "polecats" in result:
        polecats = result["polecats"]
        if not polecats:
            lines.append("No polecats found")
        else:
            lines.append(f"Found {len(polecats)} polecat(s):\n")
            for p in polecats:
                lines.append(format_polecat(p))
                lines.append("")
    
    return "\n".join(lines)


def format_polecat(p: dict) -> str:
    """Format a single polecat for display."""
    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "killed": "💀",
        "timeout": "⏰",
    }
    icon = status_icons.get(p["status"], "❓")
    
    lines = [
        f"{icon} {p['id']} [{p['status']}]",
        f"   Workdir: {p['workdir']}",
        f"   Task: {p['task'][:50]}{'...' if len(p['task']) > 50 else ''}",
        f"   Tools: {p['allowed_tools']}",
    ]
    
    if p.get("started_at"):
        lines.append(f"   Started: {p['started_at']}")
    if p.get("finished_at"):
        lines.append(f"   Finished: {p['finished_at']}")
    if p.get("exit_code") is not None:
        lines.append(f"   Exit code: {p['exit_code']}")
    if p.get("error_message"):
        lines.append(f"   Error: {p['error_message']}")
    
    return "\n".join(lines)


def main():
    """Entry point for polectl CLI."""
    parser = argparse.ArgumentParser(
        description="Control polecat supervisor daemon",
        prog="polectl"
    )
    parser.add_argument(
        "-s", "--socket",
        default="~/.polecat/daemon.sock",
        help="Daemon socket path"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # spawn command
    spawn_parser = subparsers.add_parser("spawn", help="Spawn a new polecat")
    spawn_parser.add_argument("-w", "--workdir", required=True, help="Working directory")
    spawn_parser.add_argument("-f", "--file", help="Task file path")
    spawn_parser.add_argument("-t", "--tools", default="Read", help="Allowed tools")
    spawn_parser.add_argument("task", nargs="?", help="Inline task (if no -f)")
    
    # status command
    status_parser = subparsers.add_parser("status", help="Show polecat status")
    status_parser.add_argument("id", nargs="?", help="Polecat ID")
    status_parser.add_argument("--running", action="store_true", help="Show only running")
    status_parser.add_argument("--completed", action="store_true", help="Show only completed")
    status_parser.add_argument("--failed", action="store_true", help="Show only failed")
    
    # logs command
    logs_parser = subparsers.add_parser("logs", help="Show polecat logs")
    logs_parser.add_argument("id", help="Polecat ID")
    logs_parser.add_argument("--tail", type=int, default=100, help="Number of lines")
    
    # kill command
    kill_parser = subparsers.add_parser("kill", help="Kill a running polecat")
    kill_parser.add_argument("id", help="Polecat ID")
    
    # config command
    config_parser = subparsers.add_parser("config", help="Get or set daemon config")
    config_parser.add_argument("key", nargs="?", help="Config key")
    config_parser.add_argument("value", nargs="?", type=int, help="Config value")
    
    args = parser.parse_args()
    app = PolectlApp(socket_path=args.socket)
    
    if args.command == "spawn":
        result = app.spawn(
            workdir=args.workdir,
            task=args.task,
            task_file=args.file,
            allowed_tools=args.tools
        )
        if args.json:
            print(json.dumps(result, indent=2))
        elif result["status"] == "ok":
            print(f"Spawned: {result['id']}")
        else:
            print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
    
    elif args.command == "status":
        status_filter = None
        if args.running:
            status_filter = "running"
        elif args.completed:
            status_filter = "completed"
        elif args.failed:
            status_filter = "failed"
        
        result = app.status(polecat_id=args.id, status_filter=status_filter)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(format_status(result))
    
    elif args.command == "logs":
        result = app.logs(args.id, tail=args.tail)
        if args.json:
            print(json.dumps(result, indent=2))
        elif result["status"] == "ok":
            print(result.get("content", ""))
        else:
            print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
    
    elif args.command == "kill":
        result = app.kill(args.id)
        if args.json:
            print(json.dumps(result, indent=2))
        elif result["status"] == "ok":
            print(f"Killed: {args.id}")
        else:
            print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
    
    elif args.command == "config":
        result = app.config(key=args.key, value=args.value)
        if args.json:
            print(json.dumps(result, indent=2))
        elif result["status"] == "ok":
            if "config" in result:
                for k, v in result["config"].items():
                    print(f"{k}: {v}")
            else:
                print("Config updated")
        else:
            print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
