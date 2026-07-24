# 34 — Add Index on room_members(agent_id)

**Severity**: important
**Area**: performance
**Effort**: tiny (1 line)

## Problem

`server.py:88-93` — `room_members` PK is `(room_id, agent_id)`. This indexes lookups by `room_id` first. But multiple critical queries filter by `agent_id`:

- `list_rooms` (line 396): `JOIN room_members rm ... WHERE rm.agent_id = ?`
- `_fetch()` in unread (line 539, 547): three JOINs all filter `WHERE rm.agent_id = ?`

Without an index on `agent_id`, these require a full scan of `room_members` every time. This is the hottest query path (every agent poll hits it).

## Implementation

In `init_db()`, after the `room_members` table creation, add:

```python
await _db.execute("CREATE INDEX IF NOT EXISTS idx_room_members_agent ON room_members(agent_id, room_id)")
```

## Verification

1. `make test-api` — passes.
2. `sqlite3 data/chait.db ".indexes room_members"` — shows the new index.
3. Performance: with 50+ agents, poll latency should improve measurably.
