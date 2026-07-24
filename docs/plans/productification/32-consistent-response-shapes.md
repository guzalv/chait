# 32 — Consistent POST/GET Response Shapes

**Severity**: important
**Area**: api
**Effort**: medium

## Problem

POST responses differ from GET responses for the same resource:

- `POST /rooms/{room}/messages` (line 457) returns `{id, room, author_name, text, created_at}` — missing `author_id`, `author_role`, `reply_to`, `priority`.
- `POST /dm/{id}` (line 501) returns `{id, from_id, to_id, text, created_at}` — missing `from_name`, `priority`.
- Agents must issue a follow-up GET to see the complete object they just created.

## Implementation

### Step 1: Define canonical response shapes

Create helper functions (or use the `_msg_dict()` that already exists at line 206):

```python
def _msg_dict(m) -> dict:
    d = dict(m)
    return {
        "id": d["id"], "room": d.get("room_name", ""),
        "author_id": d["author_id"], "author_name": d["author_name"],
        "author_role": d["author_role"], "text": d["text"],
        "reply_to": d.get("reply_to"), "priority": bool(d["priority"]),
        "created_at": d["created_at"]
    }
```

### Step 2: Use the same shape in POST responses

In `post_message` (around line 450-457), instead of manually constructing the response dict, use `_msg_dict()` or return the same fields.

Same for `send_dm` — use `_dm_dict()` (line 215) or equivalent.

### Step 3: If doing plan 29 (deduplicate), this comes for free

The `_impl` functions return a canonical dict that both agent and UI endpoints use.

## Verification

1. `make test-api` — update any tests that assert on response shapes.
2. POST a message, verify the response includes all fields that GET returns.

## Dependencies

Best done alongside or after plan 29 (deduplicate endpoints).
