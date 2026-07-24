# 06 — Add LIMIT to UI Messages Query

**Severity**: important
**Area**: performance
**Effort**: tiny (1 line)

## Problem

`server.py:997` — the UI messages endpoint fetches ALL messages in a room with no LIMIT:

```python
msgs = await db.execute_fetchall("SELECT * FROM messages WHERE room_id = ? ORDER BY created_at", (room_id,))
```

The agent API (`server.py:477`) correctly limits to 50. A room with 50,000 messages returns a multi-MB JSON response, consuming server RAM and choking the browser.

## Implementation

At `server.py:997`, change:

```python
# Before
msgs = await db.execute_fetchall(
    "SELECT * FROM messages WHERE room_id = ? ORDER BY created_at", (room_id,))

# After
msgs = await db.execute_fetchall(
    "SELECT * FROM messages WHERE room_id = ? ORDER BY created_at DESC LIMIT 200", (room_id,))
msgs = list(reversed(msgs))  # restore chronological order
```

Same pattern for the `since` branch at line 995 — add `LIMIT 200` there too as a safety net.

## Verification

1. `make test-api` — existing tests pass.
2. Manual: create a room, post 300 messages, load in browser — only latest 200 show.
