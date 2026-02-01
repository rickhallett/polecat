# polecat

> One paragraph: what this project does and why it exists.

---

## Quick Orient

```bash
./bin/bootstrap   # Fix environment
./bin/gate        # Verify your work
./bin/docs        # Dump context
```

---

## Architecture

See `.agent/architecture.md` for full rules.

```
src/
├── api/         # HTTP handlers
├── services/    # Business logic
├── models/      # Data types
└── utils/       # Helpers
```

---

## Current Focus

**Active:** 
**Next:** 
**Blocked:** 

---

## The Gate

Before any task is "done":
```bash
./bin/gate
```

Exit code 0 = ready. Non-zero = fix it first.

---

## Reference Code

When implementing, follow patterns in:
- `src/services/` — business logic style
- `src/api/` — interface patterns

---

## Prompts

Reusable prompts in `.prompts/`:
- `refactor.md` — refactoring guidelines
- `feature_spec.md` — spec before implementation
- `review.md` — audit checklist

---

## Known Gotchas

- 
