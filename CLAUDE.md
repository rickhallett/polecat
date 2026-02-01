# polecat

> Sandboxed Claude CLI runner with tool restrictions and retry logic. Polecats can read/write within their working directory but cannot escape it, use destructive commands, or execute arbitrary code against the OS.

---

## Quick Orient

```bash
polecat --help              # Usage
polecat -v "task"           # Verbose mode
./bin/gate                  # Verify before commit
```

---

## Architecture

Single-file bash script with no dependencies beyond Claude CLI and standard unix tools.

```
polecat              # Main script (~200 lines)
├── Tool allowlists  # Hardcoded safe tools
├── Tool denylists   # Hardcoded dangerous tools
├── PTY wrapper      # script -q /dev/null -c "..."
└── Retry loop       # Configurable max tries
```

---

## Current Focus

**Active:** v0.2.0 stable, basic sandboxing works
**Next:** Custom tool profiles, model selection, cost budgets
**Blocked:** —

---

## The Gate

Before any commit:
```bash
./bin/gate
```

Exit 0 = ready. Non-zero = fix first.

---

## Known Gotchas

### 1. PTY Required
Claude CLI requires a TTY even in `-p` (print) mode. 
**Fix:** Wrap with `script -q /dev/null -c "..."`

### 2. Argument Parsing
Tool flags like `--allowed-tools "Bash(cat:*)"` confuse the prompt parser.
**Fix:** Use `--` separator before the prompt argument

### 3. Bash Arithmetic with set -e
`((i++))` returns exit code 1 when i=0 (bash treats 0 as falsy), triggering errexit.
**Fix:** Use `((i++)) || true`

### 4. ANSI Escape Sequences
Claude CLI output includes terminal escapes even in -p mode.
**Fix:** Strip with sed pipeline (see script)

---

## Tool Restrictions

### Allowed (within workdir)
```
Read, Write(./**), Edit(./**), Glob, Grep, LS
Bash: cat, ls, head, tail, wc, grep, find, echo, pwd, date
```

### Denied
```
Bash: rm, sudo, curl, wget, chmod, chown, mv, cp, ssh, scp, rsync
Write(../*), Write(/*), Edit(../*), Edit(/*)
```

---

## Reference

- Claude CLI docs: `claude --help`
- Tool permission syntax: `--allowed-tools "Read,Write(./**),Bash(git:*)"`
- Exit codes: 0=success, non-zero=failure (retry or propagate)

---

## Prompts

See `.prompts/` for reusable patterns:
- `refactor.md` — refactoring guidelines
- `feature_spec.md` — spec before implementation
- `review.md` — audit checklist
