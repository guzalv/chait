# 38 — Idempotent Agent Join

**Severity**: important
**Area**: api, reliability
**Effort**: small

## Problem

`server.py:301-341` — every `POST /api/v1/join` creates a new agent row with a new ID and token, even if the same name+role+join_token was used before. If an agent crashes and `launch.sh` restarts it, the agent gets a new identity. The old one is orphaned (no delete endpoint). The room accumulates ghost members.

## Implementation

### Option A: Deduplicate on (name, room_id) — recommended

In the join endpoint, before creating a new agent, check if one already exists:

```python
# After resolving room_id from join_token (around line 314-318)
existing_agent = await db.execute_fetchall(
    "SELECT a.id, a.token FROM agents a JOIN room_members rm ON a.id = rm.agent_id "
    "WHERE a.name = ? AND rm.room_id = ?", (name, room_id))
if existing_agent:
    agent = dict(existing_agent[0])
    # Update card if provided
    if card:
        await db.execute("UPDATE agents SET card = ? WHERE id = ?", (json.dumps(card), agent["id"]))
        await db.commit()
    return {"id": agent["id"], "name": name, "token": agent["token"], "room": room_name}
```

### Option B: Add a self-delete endpoint

```python
@app.delete("/api/v1/me")
async def deregister(agent: dict = Depends(auth_agent)):
    db = await get_db()
    await db.execute("DELETE FROM room_members WHERE agent_id = ?", (agent["id"],))
    await db.execute("DELETE FROM agents WHERE id = ?", (agent["id"],))
    await db.commit()
    return {"status": "deregistered"}
```

Option A is better — it prevents the problem rather than cleaning up after it.

## Verification

1. `make test-api` — passes.
2. New test: join twice with same name+join_token, verify same agent ID and token returned both times.
3. Test: join with same name but different join_token (different room) — creates a new agent (different rooms).
4. `launch.sh` restart test: kill an agent process, re-run launch.sh — agent re-uses identity.
