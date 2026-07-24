# 04 — Fix get_db() Assert

**Severity**: critical
**Area**: reliability
**Effort**: tiny (1 line)

## Problem

`server.py:60` uses `assert _db is not None`. If `_db` is `None` (DB init failed), this raises `AssertionError` with no useful message. Worse: `python -O` strips asserts entirely, so `None` would propagate and cause cryptic errors elsewhere.

## Implementation

In `server.py:60`, replace:

```python
assert _db is not None
```

with:

```python
if _db is None:
    raise HTTPException(503, "Database unavailable")
```

Requires `HTTPException` import which already exists.

## Verification

1. `make test-api` — existing tests pass.
2. Optional: test by commenting out `init_db()` call, hitting any endpoint, confirm 503 response with clear message.
