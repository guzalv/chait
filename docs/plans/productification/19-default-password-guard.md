# 19 — Refuse to Start with Default Password

**Severity**: critical
**Area**: security, ops
**Effort**: tiny

## Problem

`server.py:34` — `HUMAN_PASS = os.getenv("CHAIT_HUMAN_PASS", "changeme")`. Any deployment that forgets the env var has an open admin panel with a known password.

## Implementation

In `server.py`, in the `lifespan()` function (around line 235), before `await init_db()`, add:

```python
if HUMAN_PASS == "changeme":
    import sys
    print("ERROR: CHAIT_HUMAN_PASS is set to default 'changeme'. Set a real password via environment variable.", file=sys.stderr)
    sys.exit(1)
```

Or, softer approach — auto-generate and print:

```python
if HUMAN_PASS == "changeme":
    import secrets as _s
    HUMAN_PASS = _s.token_urlsafe(16)
    print(f"WARNING: No CHAIT_HUMAN_PASS set. Generated: {HUMAN_PASS}")
```

Note: the variable needs to be declared global in lifespan if modified, or use a module-level check.

The hard-fail approach is better for production. Use the generate approach only if you want zero-config dev mode.

For tests: test fixtures already set `CHAIT_HUMAN_PASS` explicitly, so they won't be affected.

## Verification

1. `make server` without env var — should fail or print generated password.
2. `CHAIT_HUMAN_PASS=mysecret make server` — starts normally.
3. `make test-api` — passes (test fixtures set the password).
