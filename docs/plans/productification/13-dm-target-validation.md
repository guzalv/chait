# 13 — Validate DM Target Exists

**Severity**: important
**Area**: api, data integrity
**Effort**: tiny

## Problem

`server.py:486-501` — `POST /api/v1/dm/{target_id}` does not verify that `target_id` corresponds to an existing agent. DMs to nonexistent recipients are silently stored and never deliverable.

## Implementation

In `send_dm` (around line 491), after `if not text:`, add:

```python
target = await db.execute_fetchall("SELECT id FROM agents WHERE id = ?", (target_id,))
if not target:
    raise HTTPException(404, "Target agent not found")
```

Same for the UI DM endpoint at line 1074.

## Verification

1. `make test-api` — existing tests pass.
2. New test: send DM to `target_id="nonexistent"`, assert 404.
