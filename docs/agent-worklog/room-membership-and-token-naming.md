# Room Membership and Token Naming

## Changes
- Collapsed the many-to-many `room_members` join table into a direct `agents.room_id NOT NULL` foreign key. An agent can only ever belong to one room (this was already true in practice — `join_with_token` never adds an existing agent to a second room), so the join table was unnecessary indirection that complicated 17 query sites across server.py.
- Deleted the `room_members` table, its `idx_room_members_agent` index, and the `("room_id", "agents", "NULL")` migration entry. Replaced the index with `idx_agents_room ON agents(room_id)` for notification queries.
- Rewrote all queries that joined through `room_members` to use `agents.room_id` directly: `_require_room_member`, `list_rooms`, `get_room`, `post_message`, `get_messages`, `upload_document`, `list_documents`, `unread` (room messages + documents), `ui_room_details`, `ui_send_message`, `ui_upload_document`, `deregister`, `ui_remove_agent`, and the idempotent-join check in `join_with_token`.
- Renamed the agent bearer credential from `token` to `agent_token` across the entire codebase: DB column (`agents.agent_token`), JSON wire format (`POST /api/v1/join` response), INSTRUCTIONS markdown, `launch.sh` (JSON field extraction), README.md, and all three test files (`test_api.py`, `test_integration.py`, `test_ui.py`).

## Decisions
- Used subqueries (`SELECT room_id FROM agents WHERE id = ?`) for the unread endpoint's room-scoped queries rather than an explicit JOIN, since an agent is always in exactly one room — the subquery is simpler and the optimizer handles it identically.
- Renamed both the DB column and the wire-format field to `agent_token` for full consistency. The internal Python variable was already named `agent_token` in `join_with_token`, so this just closes the gap.
- Left `_notify_room_members` as the function name (descriptive, not a table reference).

## Testing
- All 113 tests pass: 50 API (`test_api.py`), 16 integration (`test_integration.py`), 47 UI (`test_ui.py`).
- Manual end-to-end verification: started server, created API token, created room, joined agent, posted message, polled `/me/unread`, confirmed room details with members — all endpoints return 200 with correct data.
