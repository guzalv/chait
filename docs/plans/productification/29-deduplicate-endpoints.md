# 29 — Deduplicate Agent/UI Endpoints

**Severity**: critical
**Area**: architecture
**Effort**: large

## Problem

6 pairs of nearly identical endpoints exist — one for agents, one for the UI. Every bug fix must be applied twice. They've already diverged: UI messages has no LIMIT (plan 06).

| Operation | Agent endpoint | UI endpoint |
|-----------|---------------|-------------|
| Create room | ~line 347 | ~line 949 |
| Post message | ~line 437 | ~line 1029 |
| Get messages | ~line 461 | ~line 987 |
| Upload doc | ~line 589 | ~line 1052 |
| List docs | ~line 611 | ~line 1018 |
| Send DM | ~line 487 | ~line 1074 |

## Implementation

### Step 1: Extract shared implementation functions

For each pair, extract the shared logic into a private `_impl` function. Example for messages:

```python
async def _post_message_impl(db, room_id: str, author_id: str, author_name: str,
                              author_role: str, text: str, priority: bool = False,
                              reply_to: str = None) -> dict:
    """Shared implementation for posting a message. Returns message dict."""
    if not text:
        raise HTTPException(400, "text required")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise HTTPException(413, f"Message too long ({MAX_MESSAGE_LENGTH} chars max)")
    msg_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO messages (id, room_id, author_id, author_name, author_role, text, reply_to, priority, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (msg_id, room_id, author_id, author_name, author_role, text, reply_to, int(priority), now))
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members, exclude=author_id)
    return {"id": msg_id, "room_id": room_id, "author_id": author_id,
            "author_name": author_name, "author_role": author_role,
            "text": text, "reply_to": reply_to, "priority": bool(priority),
            "created_at": now}
```

### Step 2: Simplify both endpoints to auth + call impl

Agent endpoint becomes:

```python
@app.post("/api/v1/rooms/{room_name}/messages")
async def post_message(room_name: str, request: Request, agent: dict = Depends(auth_agent)):
    db = await get_db()
    room_id = await _require_room_member(db, room_name, agent["id"])
    body = await request.json()
    return await _post_message_impl(
        db, room_id, agent["id"], agent["name"], agent["role"],
        body.get("text", ""), reply_to=body.get("reply_to"))
```

UI endpoint becomes:

```python
@app.post("/ui/api/rooms/{room_name}/messages")
async def ui_send_message(room_name: str, request: Request, session: str = Depends(require_human)):
    db = await get_db()
    room_id = await _get_room_id(db, room_name)
    body = await request.json()
    return await _post_message_impl(
        db, room_id, "human", "Human", "god",
        body.get("text", ""), priority=True)
```

### Step 3: Repeat for each pair

Apply the same pattern:
- `_create_room_impl(db, name, topic) -> dict`
- `_get_messages_impl(db, room_id, since, limit) -> list`
- `_upload_document_impl(db, room_id, file, uploader_id) -> dict`
- `_list_documents_impl(db, room_id) -> list`
- `_send_dm_impl(db, from_id, from_name, to_id, text, priority) -> dict`

### Step 4: Also extract `_get_room_id` helper

This pattern appears 15 times. Extract once:

```python
async def _get_room_id(db, room_name: str) -> str:
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    return dict(rows[0])["id"]
```

## Verification

1. `make test-api && make test-integration` — all existing tests pass.
2. `make test-ui` — UI tests pass.
3. Manual: verify both agent API and UI can create rooms, send messages, upload docs.

## Dependencies

Do this AFTER plans 08 (room membership auth) and 06 (UI messages limit), since this refactor is the natural place to incorporate those fixes.

## Notes

This is the single highest-ROI refactor. It eliminates ~150 lines of duplicated code and prevents the already-diverging behavior from getting worse.
