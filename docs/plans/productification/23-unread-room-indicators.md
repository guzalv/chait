# 23 — Unread Room Indicators

**Severity**: critical
**Area**: ui
**Effort**: medium

## Problem

The sidebar lists rooms with status badges but has no unread activity indicators. The human must click into each room to check for new messages. This is the single most important monitoring signal and it's missing.

## Implementation

### Step 1: Track last-read timestamp per room (client-side)

In the dashboard JS, add:

```javascript
const lastRead = {};  // {roomName: isoTimestamp}
```

When the human views a room, update `lastRead[roomName]` to the latest message timestamp.

Persist in `sessionStorage` so it survives page refresh:

```javascript
function markRead(roomName, ts) {
    lastRead[roomName] = ts;
    sessionStorage.setItem('chait_lastRead', JSON.stringify(lastRead));
}
// On load:
Object.assign(lastRead, JSON.parse(sessionStorage.getItem('chait_lastRead') || '{}'));
```

### Step 2: Add a lightweight endpoint for room activity

Add a UI API endpoint that returns the latest message timestamp per room:

```python
@app.get("/ui/api/rooms/activity")
async def ui_rooms_activity(session: str = Depends(require_human)):
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT r.name, MAX(m.created_at) as last_activity "
        "FROM rooms r LEFT JOIN messages m ON r.id = m.room_id "
        "GROUP BY r.id")
    return {dict(r)["name"]: dict(r)["last_activity"] for r in rows}
```

### Step 3: Show indicators in sidebar

In `loadRooms()`, after fetching rooms, also fetch activity. Compare with `lastRead`. Show a dot for rooms with newer activity:

```javascript
async function loadRooms() {
    const [rooms, activity] = await Promise.all([
        api('/ui/api/rooms'), api('/ui/api/rooms/activity')
    ]);
    // ... render rooms with unread dot:
    const hasUnread = activity[r.name] && (!lastRead[r.name] || activity[r.name] > lastRead[r.name]);
    // Add dot: <span class="unread-dot"></span>
}
```

CSS for the dot:

```css
.unread-dot { width: 8px; height: 8px; background: #3b82f6; border-radius: 50%; display: inline-block; margin-left: 4px; }
```

### Step 4: Clear on room selection

In `selectRoom()`, call `markRead(name, latestTimestamp)`.

## Verification

1. Manual: open dashboard, post a message to a room via API, verify dot appears in sidebar.
2. Click the room — dot disappears.
3. Refresh page — last-read state preserved.
