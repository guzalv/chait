# 11 — Restrict Agent Roles

**Severity**: high
**Area**: security
**Effort**: small

## Problem

`server.py:307` — the join endpoint accepts any string as `role`. An agent joining with `role: "god"` appears indistinguishable from human messages to other agents, enabling social engineering ("I'm the human operator, do X").

## Implementation

Add a role allowlist constant near the top of server.py:

```python
ALLOWED_AGENT_ROLES = {"agent", "reviewer", "coder", "planner", "architect", "tester", "pm", "lead", "coordinator"}
```

In the join endpoint (around line 307), after `role = body.get("role", "agent")`, add:

```python
if role not in ALLOWED_AGENT_ROLES:
    raise HTTPException(400, f"role must be one of: {', '.join(sorted(ALLOWED_AGENT_ROLES))}")
```

Keep the list open to extension (it's just a set, easy to add roles later). The key constraint is that `"god"`, `"human"`, `"admin"` are excluded.

Alternative (simpler, less restrictive): just block the reserved roles:

```python
RESERVED_ROLES = {"god", "human", "admin", "system"}
if role in RESERVED_ROLES:
    raise HTTPException(400, f"role '{role}' is reserved")
```

The blocklist approach is more practical — it doesn't break agents that use custom role names.

## Verification

1. `make test-api` — existing tests pass (check if any test uses a reserved role).
2. New test: join with `role: "god"` returns 400.
3. New test: join with `role: "coder"` succeeds.
