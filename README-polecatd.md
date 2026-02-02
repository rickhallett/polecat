# polecatd - Polecat Supervisor Daemon

A supervisor daemon for managing sandboxed Claude CLI runners (polecats) with proper PTY allocation, state persistence, and blast radius controls.

## Installation

```bash
cd ~/code/polecat
pip install -e ".[dev]"
```

## Components

### polecatd (Daemon)

The supervisor daemon that manages polecat instances.

```bash
# Start daemon
polecatd start

# Check status
polecatd status

# Stop daemon
polecatd stop
```

### polectl (CLI)

Control interface for the daemon.

```bash
# Spawn a new polecat
polectl spawn -w /path/to/workdir "Task description here"
polectl spawn -w /path/to/workdir -f task.md  # From file
polectl spawn -w /path/to/workdir -t "Read,Write(./**)" "Task"  # With tools

# Check status
polectl status              # All polecats
polectl status <id>         # Specific polecat
polectl status --running    # Only running
polectl status --completed  # Only completed
polectl status --failed     # Only failed

# View logs
polectl logs <id>           # Full log
polectl logs <id> --tail 50 # Last 50 lines

# Kill a running polecat
polectl kill <id>

# Configure daemon
polectl config                       # Show current config
polectl config max_concurrent 8      # Set max concurrent
polectl config timeout_seconds 1200  # Set timeout
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      polecatd (Daemon)                       │
├─────────────────────────────────────────────────────────────┤
│  Unix Socket Server (~/.polecat/daemon.sock)                 │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐    │
│  │ LimitEnforcer │  │ PolecatState  │  │WorkerManager  │    │
│  │ (concurrency) │  │ (SQLite DB)   │  │ (pexpect PTY) │    │
│  └───────────────┘  └───────────────┘  └───────────────┘    │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ JSON/Unix Socket
                          │
┌─────────────────────────────────────────────────────────────┐
│                      polectl (CLI)                           │
└─────────────────────────────────────────────────────────────┘
```

## State Management

State is persisted in SQLite at `~/.polecat/state.db`:

- **Polecat records**: ID, workdir, task, tools, status, timestamps
- **Statuses**: pending, running, completed, failed, killed, timeout

Logs are stored at `~/.polecat/logs/<polecat_id>.log`.

## Blast Radius Controls

- **max_concurrent**: Maximum running polecats (default: 4)
- **timeout_seconds**: Per-polecat timeout (default: 600s)
- **max_output_bytes**: Output truncation limit (default: 10MB)

## Tool Restrictions

Tool restrictions are passed through to the underlying Claude CLI:

```bash
polectl spawn -w /path -t "Read,Write(./**),Bash(git:*)" "Task"
```

## JSON Output

All commands support `--json` for machine-readable output:

```bash
polectl status --json | jq '.polecats | length'
```

## Development

```bash
# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src/polecat_supervisor --cov-report=term-missing

# Install in development mode
pip install -e ".[dev]"
```

## Files

```
~/.polecat/
├── daemon.sock     # Unix socket (when running)
├── state.db        # SQLite state database
└── logs/           # Polecat log files
    ├── pc-abc123.log
    └── pc-def456.log
```
