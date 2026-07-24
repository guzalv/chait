# 26 — Room Status Control from UI

**Severity**: important
**Area**: ui
**Effort**: small

## Problem

The API supports `POST /api/v1/rooms/{room}/status` (line 419) but the UI has no control to change room status. If agents get stuck, the human must use curl to mark a room as `blocked` or `completed`.

## Implementation

### Step 1: Add status selector to room header

In the `loadRoomDetails()` JS function (around line 853), after rendering the room topic, add a status dropdown:

```javascript
const statusOpts = ['active', 'waiting-for-input', 'completed', 'blocked'];
const statusSelect = `
<select id="room-status-select" onchange="changeRoomStatus(this.value)" style="...">
    ${statusOpts.map(s => `<option value="${s}" ${s === room.status ? 'selected' : ''}>${s}</option>`).join('')}
</select>`;
```

### Step 2: Add a UI API endpoint for status change

Add to server.py:

```python
@app.post("/ui/api/rooms/{room_name}/status")
async def ui_set_room_status(room_name: str, request: Request, session: str = Depends(require_human)):
    db = await get_db()
    body = await request.json()
    new_status = body.get("status")
    if new_status not in VALID_ROOM_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(VALID_ROOM_STATUSES)}")
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room_id = dict(rows[0])["id"]
    await db.execute("UPDATE rooms SET status = ? WHERE id = ?", (new_status, room_id))
    await db.commit()
    return {"room": room_name, "status": new_status}
```

Or reuse the shared implementation once plan 29 (deduplicate endpoints) is done.

### Step 3: Add JS handler

```javascript
async function changeRoomStatus(status) {
    await api(`/ui/api/rooms/${encodeURIComponent(currentRoom)}/status`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({status})
    });
}
```

## Verification

1. Manual: select a room, change status via dropdown, verify it persists on page reload.
2. `make test-ui` — passes.
