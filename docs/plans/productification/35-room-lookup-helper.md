# 35 — Extract Room Lookup Helper

**Severity**: important
**Area**: architecture
**Effort**: small

## Problem

The pattern `SELECT id FROM rooms WHERE name = ?` + check empty + extract `room_id` appears 15 times across server.py. Each occurrence is 3-4 lines of boilerplate.

## Implementation

This is likely done as part of plan 08 (room membership auth) which introduces `_require_room_member()`, or plan 29 (deduplicate endpoints). But for the UI endpoints (which don't need membership checks since the human is god), a simpler helper is needed:

```python
async def _get_room_id(db: aiosqlite.Connection, room_name: str) -> str:
    """Resolve room name to ID. Raises 404 if not found."""
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    return dict(rows[0])["id"]
```

Then replace all 15 occurrences of the pattern with a single call:

```python
room_id = await _get_room_id(db, room_name)
```

## Verification

1. `make test-api && make test-integration` — passes.
2. Grep for the old pattern — should be gone.

## Dependencies

Best done alongside plan 08 and plan 29. The helpers can coexist: `_get_room_id` for UI/admin paths, `_require_room_member` for agent paths (which calls `_get_room_id` internally).
