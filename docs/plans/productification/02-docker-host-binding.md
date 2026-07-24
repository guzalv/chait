# 02 — Fix Docker Host Binding

**Severity**: critical
**Area**: ops
**Effort**: tiny (1 line)

## Problem

`server.py:1126` binds to `127.0.0.1`. Inside a Docker container, this makes the server unreachable from outside even with `-p 3100:3100`. Docker deployment is completely broken.

## Implementation

In `server.py:1126`, change:

```python
# Before
uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")

# After
host = os.getenv("CHAIT_HOST", "0.0.0.0")
uvicorn.run(app, host=host, port=PORT, log_level="info")
```

## Verification

1. `docker build -t chait . && docker run -p 3100:3100 chait` — server should be reachable at `http://localhost:3100` from the host.
2. Non-Docker `make server` still works (binds to `0.0.0.0` by default, which is fine for dev too).
