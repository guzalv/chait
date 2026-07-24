# 24 — Connection Status and Error Handling in UI

**Severity**: critical
**Area**: ui
**Effort**: small

## Problem

1. The `api()` JS function (line 812) has no error handling. Network failures or server errors silently break polling — the UI shows stale data with no indication.
2. No visual indicator of connection/poll status.
3. No feedback when operations (send message, create room, upload) fail.

## Implementation

### Step 1: Add error handling to `api()`

Replace the `api()` function:

```javascript
let lastPollOk = Date.now();

async function api(p, o) {
    try {
        const r = await fetch(p, o);
        if (r.status === 303 || r.redirected) { location = '/login'; return null; }
        if (!r.ok) {
            const err = await r.text().catch(() => r.statusText);
            console.error(`API ${r.status}: ${p}`, err);
            return null;
        }
        lastPollOk = Date.now();
        updateStatus('ok');
        return r.json();
    } catch (e) {
        console.error('Network error:', p, e);
        updateStatus('error');
        return null;
    }
}
```

### Step 2: Add status indicator to header

In the dashboard HTML header area, add:

```html
<span id="conn-status" style="font-size:.65rem;margin-left:auto;padding:2px 6px;border-radius:3px;background:#22c55e;color:#fff">Live</span>
```

Add the JS update function:

```javascript
function updateStatus(state) {
    const el = document.getElementById('conn-status');
    if (state === 'ok') {
        el.textContent = 'Live';
        el.style.background = '#22c55e';
    } else {
        el.textContent = 'Disconnected';
        el.style.background = '#ef4444';
    }
}

// Detect stale data (no successful poll in 15s)
setInterval(() => {
    if (Date.now() - lastPollOk > 15000) updateStatus('error');
}, 5000);
```

### Step 3: Add null checks to callers

Every function that calls `api()` needs to handle `null` return:

```javascript
async function loadRooms() {
    const rooms = await api('/ui/api/rooms');
    if (!rooms) return;  // don't clear the room list on error
    // ... existing rendering
}

async function pollMessages() {
    const msgs = await api(`/ui/api/rooms/${encodeURIComponent(currentRoom)}/messages?since=${lastTs}`);
    if (!msgs) return;  // keep existing messages on error
    // ... existing rendering
}
```

Similarly for `sendMessage`, `createRoom`, `uploadFile`, etc. — check for null and show a brief error toast or inline message.

## Verification

1. Manual: start server, open dashboard, verify "Live" indicator.
2. Stop server — indicator turns "Disconnected" within 15 seconds.
3. Restart server — indicator returns to "Live".
4. `make test-ui` — existing tests pass.
