# 16 — Add Health Check Endpoint

**Severity**: critical
**Area**: ops
**Effort**: tiny

## Problem

No `/health` or `/healthz` endpoint. Orchestrators (Docker, K8s), load balancers, and monitoring systems have nothing to probe. If the DB connection dies or the event loop blocks, nothing reports unhealthy.

## Implementation

Add after the lifespan function or near other utility endpoints:

```python
@app.get("/health")
async def health():
    try:
        db = await get_db()
        await db.execute_fetchall("SELECT 1")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")
```

## Verification

1. `curl http://localhost:3100/health` — returns `{"status": "ok"}`.
2. Stop the DB (or corrupt the path) — returns 503.
3. Existing tests unaffected.

## Notes

Keep it simple. No auth required on health — monitoring systems need to hit it without credentials. This is standard practice.
