# 07 — Message and DM Text Length Limit

**Severity**: important
**Area**: security, performance
**Effort**: tiny

## Problem

`server.py:439` and `server.py:489` accept message text with no upper bound. An agent can send a 100 MB message, stored in SQLite and transmitted to every poller.

## Implementation

Add a constant near the top of server.py:

```python
MAX_MESSAGE_LENGTH = 100_000  # 100 KB
```

In `post_message` (around line 441), after `if not text:`, add:

```python
if len(text) > MAX_MESSAGE_LENGTH:
    raise HTTPException(413, f"Message too long ({MAX_MESSAGE_LENGTH} chars max)")
```

Same in `send_dm` (around line 491), after `if not text:`:

```python
if len(text) > MAX_MESSAGE_LENGTH:
    raise HTTPException(413, f"Message too long ({MAX_MESSAGE_LENGTH} chars max)")
```

Also add the same check in the UI send_message endpoint (around line 1038) and UI send_dm (around line 1080).

## Verification

1. `make test-api` — existing tests pass.
2. New test: POST a message with 200,000 chars, assert 413.
