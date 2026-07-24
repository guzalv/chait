# 22 — DM Conversation View in UI

**Severity**: critical
**Area**: ui
**Effort**: medium

## Problem

The human can send DMs to agents via a modal (`server.py:881-887`) but has zero way to read DM replies. There's no UI to view DM history. The data exists (agents can read DMs via the API, `GET /api/v1/dm/{target_id}`), but the web UI has no way to display them. This makes DMs a broken feature.

## Implementation

### Option A: Inline DM panel (recommended)

When the human clicks "DM" on an agent card, instead of just showing a send modal, show a conversation view:

1. Add a UI API endpoint for human to read DMs:

```python
@app.get("/ui/api/dm/{agent_id}")
async def ui_get_dms(agent_id: str, session: str = Depends(require_human)):
    db = await get_db()
    # Human DMs are stored with from_id="human" or to_id="human"
    msgs = await db.execute_fetchall(
        "SELECT * FROM dms WHERE (from_id = 'human' AND to_id = ?) OR (from_id = ? AND to_id = 'human') ORDER BY created_at",
        (agent_id, agent_id))
    return [dict(m) for m in msgs]
```

Note: check what ID the human uses when sending DMs. The UI send_dm endpoint at line 1074 uses `from_id="human"` — verify this matches.

2. Update the `openDM()` JS function to:
   - Fetch DM history: `GET /ui/api/dm/{agent_id}`
   - Display messages in the modal body with a scrollable area
   - Keep the text input at the bottom
   - After sending, append the sent message locally and keep the modal open

3. In the DM modal HTML (around line 798-808), restructure:

```html
<div id="dm-modal" class="modal">
  <div class="modal-content">
    <h3>DM with <span id="dm-agent-name"></span></h3>
    <div id="dm-messages" style="max-height:300px; overflow-y:auto; margin-bottom:1rem;"></div>
    <textarea id="dm-text" rows="2" placeholder="Type a message..."></textarea>
    <div style="display:flex;gap:.5rem;margin-top:.5rem">
      <button class="btn" onclick="sendDM()">Send</button>
      <button class="btn" onclick="closeDM()">Close</button>
    </div>
  </div>
</div>
```

4. Add polling for DM updates while the modal is open (simple setInterval, clear on close).

### Option B: Simpler — DM tab in right panel

Add a "DMs" section to the right info panel that lists recent DM conversations. Clicking one shows the thread. Less modal-centric.

## Verification

1. `make test-ui` — existing tests pass.
2. Manual: send a DM to an agent, verify it appears in the conversation view.
3. Simulate an agent DM reply (via API), verify it appears in the human's DM view.

## Dependencies

Check what `from_id` value the UI uses for human DMs (line 1080). The DM read endpoint must match this value.
