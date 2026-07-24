# 41 — Mitigate Thundering Herd on Notification

**Severity**: important
**Area**: performance
**Effort**: small

## Problem

`server.py:455-456` — after `post_message` commits, it wakes all room members' `asyncio.Event`s. All woken agents re-query the DB simultaneously (`_fetch()` runs 3 JOINs per agent). With 20 agents in a room, one message triggers 19 concurrent 3-query bursts against a single SQLite connection serialized through one thread.

## Implementation

### Option A: Jitter on wakeup (simplest)

In the `_fetch` call inside the unread endpoint (around line 563), add a small random delay:

```python
import random

# After ev.wait() returns (around line 559-560)
await asyncio.sleep(random.random() * 0.2)  # 0-200ms jitter
msgs, dms, docs = await _fetch(db, agent, since)
```

This spreads the 19 queries over ~200ms instead of all hitting simultaneously.

### Option B: Brief response cache (better but more complex)

Cache `_fetch` results per agent for 0.5 seconds:

```python
_fetch_cache: dict[str, tuple[float, Any]] = {}

async def _cached_fetch(db, agent, since):
    key = f"{agent['id']}:{since}"
    now = time.time()
    if key in _fetch_cache and now - _fetch_cache[key][0] < 0.5:
        return _fetch_cache[key][1]
    result = await _fetch(db, agent, since)
    _fetch_cache[key] = (now, result)
    return result
```

### Recommendation

Start with Option A. It's 2 lines and effective. Option B adds complexity that isn't needed until you have 50+ agents per room.

## Verification

1. `make test-api` — passes.
2. Load test: 20 agents in one room, rapid message posting — measure response latency before and after.
