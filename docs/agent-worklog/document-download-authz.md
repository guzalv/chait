# Document Download Authorization

## Changes
- Added a room-membership check to `GET /api/v1/documents/{doc_id}/download` (`server.py`). Previously the endpoint was guarded only by `Depends(auth_any)`: it authenticated the caller but never verified they belonged to the document's room, so any authenticated agent could download any room's document by `doc_id` (cross-room IDOR / data leak, confirmed empirically with HTTP 200).
- After loading the document row, if the caller is an agent (`auth.get("type") != "human"`), verify membership with `SELECT 1 FROM agents WHERE id = ? AND room_id = ?` using `auth["id"]` and `doc["room_id"]`. Non-members get `404 NOT_FOUND` "Document not found". Humans (god-mode session) bypass the check and retain full access.
- Renamed the unused `_auth` param to `auth` since it is now used.

## Decisions
- Return `404` (not `403`) for non-members so the endpoint does not reveal that a document exists in another room, matching the existing "Document not found" response for unknown IDs.
- Inline the membership query rather than reuse `_require_room_member`: that helper resolves by room *name* and raises `403`, neither of which fits here (we have the room_id already and want a 404 non-disclosing response).

## Testing
- `make test-api`: added three tests to `tests/test_api.py::TestDocuments`:
  - `test_download_document_cross_room_agent_denied`: agent in room A gets 404 downloading room B's doc.
  - `test_download_document_member_agent`: member agent still downloads (200, correct bytes).
  - `test_download_document_human_godmode`: logged-in human downloads any doc (200, correct bytes).
- Existing download test (`test_download_document`) unchanged and still passes.
