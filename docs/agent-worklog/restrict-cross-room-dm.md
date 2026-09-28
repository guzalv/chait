# Restrict Cross-Room Direct Messages

## Changes
- Added a same-room check to `POST /api/v1/dm/{target_id}` (`send_dm`, `server.py`). Previously the endpoint only verified the target agent existed, so an agent in room A could DM any agent in room B (cross-room messaging = spam / prompt-injection vector across unrelated projects).
- The check now fetches the target's room with `SELECT room_id FROM agents WHERE id = ?` and rejects the request with `404 NOT_FOUND` "Target agent not found" if the target is missing OR its `room_id` differs from the sender's `agent["room_id"]`. Same-room DMs behave exactly as before.
- Added a one-line clause to the agent-facing API docs (`INSTRUCTIONS`, "### Direct Messages") noting DMs can only be sent to agents in your own room.
- The human web-UI DM endpoint (`ui_send_dm`, god-mode) is unchanged.

## Decisions
- Return `404` with the same message for both "missing" and "different room" so the endpoint does not disclose the existence of agents in other rooms — consistent with the document-download IDOR fix (#11).
- Agents belong to exactly one room (each `agents` row has a single `room_id`), so a single `room_id` comparison is sufficient; no membership-set lookup needed.
- Inline the room lookup rather than reuse `_require_room_member` (that helper resolves by room *name* and raises `403`, neither of which fits: we already have the sender's `room_id` and want a non-disclosing 404).

## Testing
- `make test-api`: added `tests/test_api.py::TestDMs::test_send_dm_same_room_delivered` (same-room DM → 200, delivered) and `test_send_dm_cross_room_returns_404` (different rooms → 404).
- `make test-integration`: updated `test_human_sees_room_member_dms` to assert the cross-room DM now returns 404 while the room-DM visibility assertions still hold.
- Verified `test_dm_routing` (same-room) and `test_human_dm_to_agent` (human god-mode) still pass.
- `make lint` clean.
