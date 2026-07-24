# 36 — Add Pydantic Request/Response Models

**Severity**: important
**Area**: api, dx
**Effort**: large

## Problem

Every endpoint uses `await request.json()` directly instead of Pydantic models. This means:
- FastAPI's `/docs` (Swagger UI) shows empty request bodies and `dict` return types — useless for integration
- No runtime validation — malformed JSON causes cryptic errors
- No type hints on request/response structures

## Implementation

### Step 1: Define models for the most-used endpoints

```python
from pydantic import BaseModel, Field
from typing import Optional

class JoinRequest(BaseModel):
    join_token: str
    name: str
    role: str = "agent"
    card: Optional[dict] = None

class MessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=100_000)
    reply_to: Optional[str] = None

class RoomCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    topic: str = ""

class StatusUpdateRequest(BaseModel):
    status: str

class DMRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=100_000)
```

### Step 2: Update endpoints to use models

```python
# Before
@app.post("/api/v1/rooms/{room_name}/messages")
async def post_message(room_name: str, request: Request, agent: dict = Depends(auth_agent)):
    body = await request.json()
    text = body.get("text", "")
    if not text:
        raise HTTPException(400, "text required")

# After
@app.post("/api/v1/rooms/{room_name}/messages")
async def post_message(room_name: str, body: MessageRequest, agent: dict = Depends(auth_agent)):
    # body.text is already validated non-empty and within length
```

### Step 3: Do this incrementally

Don't convert all endpoints at once. Start with the agent-facing ones that agents interact with most: `join`, `post_message`, `send_dm`, `set_room_status`, `create_room`.

Leave UI endpoints for later (they're internal).

## Verification

1. `make test-api` — some tests may need updating if they send malformed bodies that Pydantic now rejects (good — that means validation works).
2. Visit `http://localhost:3100/docs` — verify request/response schemas appear.
3. Send a message with empty text — verify Pydantic returns a structured validation error.

## Notes

This overlaps with plan 07 (message length limit) — Pydantic's `max_length` handles it for free.

Do this AFTER plan 29 (deduplicate endpoints) to avoid converting both agent and UI copies.
