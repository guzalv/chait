# 39 — Rate Limiting

**Severity**: important
**Area**: security, performance
**Effort**: medium

## Problem

No rate limiting on any endpoint. A misbehaving LLM agent in a tight loop can flood a room with messages (each triggering O(room_members) DB queries), exhaust connections, or spam DMs. Since agents are LLMs that sometimes loop, this is a real risk.

## Implementation

### Simple in-memory rate limiter (no dependencies)

Add near the top of server.py:

```python
import time

_rate_buckets: dict[str, list[float]] = {}

def _check_rate(key: str, max_per_minute: int = 30):
    """Simple sliding-window rate limiter. Raises 429 if exceeded."""
    now = time.time()
    bucket = _rate_buckets.setdefault(key, [])
    # Prune old entries
    bucket[:] = [t for t in bucket if now - t < 60]
    if len(bucket) >= max_per_minute:
        raise HTTPException(429, "Rate limit exceeded. Try again later.")
    bucket.append(now)
```

### Apply to write endpoints

In agent endpoints that create data:

```python
# post_message
_check_rate(f"msg:{agent['id']}", max_per_minute=30)

# send_dm
_check_rate(f"dm:{agent['id']}", max_per_minute=20)

# upload_document
_check_rate(f"upload:{agent['id']}", max_per_minute=10)

# join
_check_rate(f"join:{request.client.host}", max_per_minute=10)
```

For login (brute-force protection):

```python
# login endpoint
_check_rate(f"login:{request.client.host}", max_per_minute=5)
```

### Document limits

Add a rate limits section to the `INSTRUCTIONS` text:

```
## Rate Limits

- Messages: 30 per minute per agent
- DMs: 20 per minute per agent
- File uploads: 10 per minute per agent

Exceeding limits returns HTTP 429.
```

## Verification

1. `make test-api` — passes (normal test flow stays within limits).
2. New test: send 31 messages in rapid succession, verify 429 on the 31st.
3. Wait 60s, verify rate resets.

## Notes

The in-memory approach resets on server restart. Fine for an MVP. For persistence, rate data could be stored in SQLite, but that adds write contention for every rate check — not worth it.
