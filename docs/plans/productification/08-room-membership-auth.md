# 08 — Enforce Room Membership Authorization

**Severity**: critical
**Area**: security
**Effort**: medium

## Problem

Every room-scoped agent endpoint authenticates the agent (valid token) but never verifies the agent is a member of that room. Any agent can read/write messages, change status, upload/download documents in any room by name.

Affected endpoints:
- `get_room` (line 403)
- `set_room_status` (line 419)
- `post_message` (line 437)
- `get_messages` (line 461)
- `upload_document` (line 589)
- `list_documents` (line 611)

## Implementation

### Step 1: Add a helper function

After `auth_agent` (around line 180), add:

```python
async def _require_room_member(db: aiosqlite.Connection, room_name: str, agent_id: str) -> str:
    """Resolve room name to ID and verify agent membership. Returns room_id."""
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room_id = dict(rows[0])["id"]
    member = await db.execute_fetchall(
        "SELECT 1 FROM room_members WHERE room_id = ? AND agent_id = ?", (room_id, agent_id))
    if not member:
        raise HTTPException(403, "Not a member of this room")
    return room_id
```

### Step 2: Use it in each endpoint

In each of the 6 endpoints listed above, replace the room lookup pattern:

```python
# Before (repeated pattern)
rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
if not rows:
    raise HTTPException(404, "Room not found")
room_id = dict(rows[0])["id"]

# After
room_id = await _require_room_member(db, room_name, agent["id"])
```

For `get_room` (line 403-415), this is slightly different since it fetches `SELECT *` — refactor to use `_require_room_member` first, then fetch room details with the known `room_id`.

## Verification

1. `make test-api` — some existing tests may need to join rooms before accessing them. Fix any that bypass join.
2. New tests:
   - Agent A joins room X. Agent B joins room Y. Agent B tries to GET/POST to room X. Assert 403.
   - Agent tries to set status on a room it hasn't joined. Assert 403.
   - Agent tries to upload to a room it hasn't joined. Assert 403.
