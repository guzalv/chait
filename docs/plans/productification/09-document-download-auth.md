# 09 — Add Authentication to Document Download

**Severity**: critical
**Area**: security
**Effort**: small

## Problem

`server.py:623` — `download_document` has no authentication at all. No `Depends(auth_agent)`, no `Depends(require_human)`. Anyone who guesses a 12-char hex doc_id can download any file.

## Implementation

The download endpoint needs to accept either agent token auth OR human session auth. Add a combined auth dependency:

```python
async def auth_any(request: Request) -> dict:
    """Accept either agent bearer token or human session cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return await auth_agent(request)
    session_token = request.cookies.get("chait_session")
    if session_token:
        result = await auth_human(request)
        if result:
            return {"type": "human"}
    raise HTTPException(401, "Authentication required")
```

Then update the endpoint signature at line 623:

```python
# Before
async def download_document(doc_id: str):

# After
async def download_document(doc_id: str, _auth: dict = Depends(auth_any)):
```

Optionally, for agent requests, verify the agent is a member of the document's room using the document's `room_id` from the DB query at line 625.

## Verification

1. `make test-api` — existing tests pass (add auth headers to any download tests).
2. New test: unauthenticated GET to `/api/v1/documents/{id}/download` returns 401.
3. Test: authenticated agent can download docs from their room.
4. Test: human session can download any doc.
