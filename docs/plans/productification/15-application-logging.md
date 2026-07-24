# 15 — Add Application Logging

**Severity**: critical
**Area**: ops, reliability
**Effort**: medium

## Problem

`server.py` has zero application-level logging. No `import logging`, no log calls anywhere. When something fails in production, the only signal is uvicorn's access log (`GET /path 200`). Can't debug "why did agent X not receive messages" or "why did room creation fail".

## Implementation

### Step 1: Set up logger

At the top of server.py (after imports), add:

```python
import logging

logger = logging.getLogger("chait")
```

### Step 2: Add log calls at critical points

Minimum logging points (INFO level):

```python
# In init_db()
logger.info("Database initialized at %s", DB_PATH)

# In join endpoint (after successful join)
logger.info("Agent '%s' (role=%s) joined room '%s'", name, role, room_name)

# In room creation
logger.info("Room '%s' created (id=%s)", name, room_id)
```

WARNING level:

```python
# In auth_agent, on failure (line ~175)
logger.warning("Auth failed: invalid token from %s", request.client.host if request.client else "unknown")

# In login, on failure (line ~652)
logger.warning("Login failed for user '%s' from %s", form.get("user", ""), request.client.host if request.client else "unknown")
```

ERROR level:

```python
# In any except block that currently swallows errors (e.g., migration at line 143-146)
logger.error("Migration failed for %s.%s: %s", tbl, col, e)
```

### Step 3: Configure format

In the `if __name__ == "__main__"` block (before `uvicorn.run`), add:

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
```

## Verification

1. `make server` — start server, join a room, check logs show the join event.
2. Send an invalid token — check logs show auth warning.
3. `make test-api` — tests still pass (logging shouldn't affect behavior).

## Notes

Keep logging minimal. Don't log message content (privacy). Don't log tokens (security). Log events and outcomes.
