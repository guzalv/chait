# Archive & Delete Rooms

## Changes
- Rooms can now be archived (`POST /ui/api/rooms/{room}/archive`) and unarchived (`POST /ui/api/rooms/{room}/unarchive`). Archiving sets a new `archived_at` timestamp column, added via the existing ad-hoc migration list in `init_db()`.
- `GET /ui/api/rooms` now takes an `archived` query flag (default `false`) instead of adding a separate "list archived" endpoint — returns non-archived rooms by default, archived ones when `?archived=true`.
- Rooms can be permanently deleted (`DELETE /ui/api/rooms/{room}`). Deletion removes the room's `messages`, `documents`, and `agents` rows (agents must go first — `agents.room_id` has an FK to `rooms` with `PRAGMA foreign_keys=ON`), deletes the room's on-disk document directory, then the room row itself.
- Dashboard: room header gained "Archive"/"Delete" buttons (Delete has a native `confirm()`); sidebar gained an "Archived" button opening a modal listing archived rooms with "Unarchive"/"Delete" actions, mirroring the existing API-token modal pattern.
- Added `hack/dev-server.sh` — spins up a one-off `server.py` on a random port with an isolated temp data dir for manual/curl testing, backgrounded via `setsid`+`disown` so it survives after the launching shell/tool call exits.

## Decisions
- Archived is a separate `archived_at` timestamp, not a new value in `VALID_ROOM_STATUSES` — archiving is a human curation concept orthogonal to the room's workflow status (active/waiting-for-input/completed/blocked), so a completed room can also be archived without losing that status.
- Delete is hard delete with explicit cascade cleanup, following the pattern of `_get_room_id` (404 on missing room) rather than the silent-no-op pattern used by the existing agent/token delete endpoints — room deletion is destructive enough (wipes messages/docs) to warrant the existence check.
- Both delete and archive/unarchive are UI-only (`/ui/api/...`), matching the existing asymmetry where destructive actions (agent removal, token revocation) have no agent-facing (`/api/v1/...`) counterpart.

## Testing
- `make test-api` — 60 passed (8 new: archive, unarchive, list-archived, delete + cascade cleanup, 404s for both actions on a nonexistent room).
- `make test-integration` — 17 passed (no regressions).
- `make lint` — clean.
- Manual via `hack/dev-server.sh`: create → archive → confirm hidden from default list → confirm shown in `?archived=true` → unarchive → confirm restored; create → join agent → upload doc → delete → confirmed doc directory removed from disk, room gone from listing, agent's token invalidated (401), repeat delete returns 404.
