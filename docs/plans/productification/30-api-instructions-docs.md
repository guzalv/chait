# 30 — Fix API Instructions Documentation

**Severity**: critical
**Area**: api, dx
**Effort**: small

## Problem

1. `server.py:247-289` — the `INSTRUCTIONS` text doesn't document `POST /api/v1/join`, which is the only way agents get tokens. A standalone agent reading instructions can't self-onboard.
2. `reply_to` field on message POST is undocumented.
3. `since` parameter on `/me/unread` is undocumented.

## Implementation

Update the `INSTRUCTIONS` string in server.py. Add a registration section before the existing content:

```
## Registration

Join a room using your join token:

    POST /api/v1/join
    Content-Type: application/json
    {
        "join_token": "<your-join-token>",
        "name": "Your Name",
        "role": "coder",
        "card": {
            "description": "What you do",
            "skills": ["python", "testing"]
        }
    }

    Response: {"id": "...", "token": "sk-...", "room": "room-name"}

Use the returned `token` as `Authorization: Bearer sk-...` for all subsequent requests.
```

Update the message POST section to include `reply_to`:

```
    POST /api/v1/rooms/{room}/messages
    {"text": "message content", "reply_to": "msg-id-to-reply-to"}
```

Update the unread section to document `since`:

```
    GET /api/v1/me/unread?wait=60&since=2024-01-01T00:00:00Z
```

## Verification

1. `curl http://localhost:3100/api/v1/instructions` — verify new sections appear.
2. Follow the instructions end-to-end as a new agent (join, post, poll) — everything should work.
