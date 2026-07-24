# 43 — Agent Token Expiry and Revocation

**Severity**: important
**Area**: security
**Effort**: medium

## Problem

1. Agent tokens never expire — the `agents` table has `created_at` but no `expires_at`. Old tokens accumulate and remain valid indefinitely.
2. No way to revoke an agent token or kick an agent from a room. A compromised token grants permanent access.

## Implementation

### Token expiry

Add `expires_at` column to agents table. In `init_db()` migrations:

```python
# Migration
("agents", "expires_at"),
```

In the join endpoint, set expiry (default 24 hours, configurable):

```python
TOKEN_TTL_HOURS = int(os.getenv("CHAIT_TOKEN_TTL_HOURS", "24"))

# In join handler
expires = (datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)).isoformat()
await db.execute("INSERT INTO agents ... VALUES (..., ?)", (..., expires))
```

In `auth_agent` (line 167), add expiry check:

```python
agent = dict(rows[0])
if agent.get("expires_at") and agent["expires_at"] < _now():
    raise HTTPException(401, "Token expired")
```

### Token revocation / agent removal

Add a human-only endpoint:

```python
@app.delete("/ui/api/agents/{agent_id}")
async def ui_remove_agent(agent_id: str, session: str = Depends(require_human)):
    db = await get_db()
    await db.execute("DELETE FROM room_members WHERE agent_id = ?", (agent_id,))
    await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    await db.commit()
    return {"status": "removed", "agent_id": agent_id}
```

Add a self-deregister endpoint for agents:

```python
@app.delete("/api/v1/me")
async def deregister(agent: dict = Depends(auth_agent)):
    db = await get_db()
    await db.execute("DELETE FROM room_members WHERE agent_id = ?", (agent["id"],))
    await db.execute("DELETE FROM agents WHERE id = ?", (agent["id"],))
    await db.commit()
    return {"status": "deregistered"}
```

### UI: Add remove button to agent cards

In the dashboard agent cards (around line 862), add a remove button next to the DM button:

```javascript
<button class="btn btn-sm" onclick="removeAgent('${m.id}')" style="background:#ef4444">Remove</button>
```

## Verification

1. `make test-api` — passes.
2. New test: create agent, wait for expiry (or backdate `expires_at`), verify auth returns 401.
3. New test: DELETE `/api/v1/me`, verify agent can no longer auth.
4. New test: human removes agent via UI API, verify agent removed from room members.

## Notes

For `launch.sh` long-running sessions, consider token refresh: agent calls a refresh endpoint before expiry to get a new token. Or set TTL to 0 (no expiry) for dev/testing.
