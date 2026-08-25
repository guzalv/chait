# Room DM Visibility for Human

## Changes
- Added `GET /ui/api/rooms/{room_name}/dms` (`server.py`), a human-only endpoint that returns DMs where both `from_id` and `to_id` belong to agents in that room. Mirrors the existing `ui_messages` pattern: optional `since` filter, `LIMIT 200`, newest-last ordering.
- The dashboard UI (button, modal, JS) was already added as part of the markdown rendering PR (#5). This PR adds only the backend endpoint that makes it functional, plus comprehensive test coverage.

## Problem
Previously the human ("god-mode") could only read DMs where the human itself was a participant (`GET /ui/api/dm/{agent_id}`, hardcoded `from_id='human' OR to_id='human'`). Agent-to-agent DMs — an explicitly encouraged pattern per `launch.sh`'s agent instructions ("Use DMs for private side-conversations") — were completely invisible to the human, contradicting the README's "full visibility" claim. There was no query path, filtered or otherwise, that could return a third party's DM.

## Decisions
- Used `AND` (both sides must be room members) rather than `OR` for the membership check. This scopes the view to exactly the previously-invisible case — agent-to-agent DMs — without duplicating the human's own conversations, which remain visible via the existing per-agent DM modal.
- Read-only endpoint: sending is already handled by the existing per-agent DM modal.

## Testing
- API tests: auth requirement, basic response shape, 404 for nonexistent room.
- Integration test: agent-to-agent visibility, exclusion of human's own DMs, exclusion of cross-room DMs, `since` filtering.
- E2E UI tests (Selenium): button existence, modal open/close, DM content rendering with sender/recipient names, escape key dismissal.
