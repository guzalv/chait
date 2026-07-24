# 28 — UI Polish Bundle

**Severity**: important (combined)
**Area**: ui
**Effort**: medium

A collection of smaller UI improvements that are individually nice-to-have but collectively make the difference between "MVP" and "usable product."

## 28a: Textarea Auto-Resize

**Problem**: Message input has `resize:none` and fixed `rows="2"`. Multi-line messages are typed blind.

**Fix**: Remove `resize:none` from CSS. Add JS auto-grow:

```javascript
const ta = document.getElementById('msg-input');
ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
});
```

## 28b: Room State Persistence on Refresh

**Problem**: Refreshing the page loses the selected room. User sees sidebar with no room selected.

**Fix**: Persist in sessionStorage:

```javascript
async function selectRoom(name) {
    currentRoom = name;
    sessionStorage.setItem('chait_room', name);
    // ... existing code
}
// On load:
const saved = sessionStorage.getItem('chait_room');
if (saved) selectRoom(saved);
```

## 28c: Empty States

**Problem**: Blank sidebar when no rooms exist. Blank message area when room has no messages. No guidance for new users.

**Fix**: Add placeholder text:

```javascript
// In loadRooms, if rooms.length === 0:
el.innerHTML = '<div style="color:#64748b;padding:1rem;font-size:.8rem">No rooms yet. Click "+ Room" to create one.</div>';

// In renderMessages, if msgs.length === 0:
el.innerHTML = '<div style="color:#64748b;padding:2rem;text-align:center;font-size:.85rem">No messages yet.</div>';
```

## 28d: Body Font Change

**Problem**: Monospace for all text is fatiguing for chat (natural language, not code).

**Fix**: Change body font, keep monospace for tokens:

```css
body { font-family: system-ui, -apple-system, sans-serif; ... }
.token-display, code, .api-token { font-family: 'SF Mono', 'Fira Code', monospace; }
```

## 28e: Completed Rooms Visual De-emphasis

**Problem**: Completed/blocked rooms look identical to active ones in sidebar (tiny badge is the only difference).

**Fix**:
```css
.room-item[data-status="completed"] { opacity: 0.5; }
.room-item[data-status="blocked"] { opacity: 0.7; border-left: 2px solid #ef4444; }
```

In `loadRooms()`, add `data-status="${r.status}"` to each room item.

Also sort: active/waiting first, completed/blocked last.

## Verification

1. Manual test each sub-item.
2. `make test-ui` — passes.
