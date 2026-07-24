# 33 — Structured Error Responses

**Severity**: important
**Area**: api
**Effort**: medium

## Problem

All errors use FastAPI's default `HTTPException(status_code, "string")`, producing `{"detail": "text required"}`. No error codes, no field names, no request IDs. Agents parsing errors must match on human-readable strings, which is fragile.

## Implementation

### Step 1: Define error structure

```python
class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, field: str = None):
        detail = {"code": code, "message": message}
        if field:
            detail["field"] = field
        super().__init__(status_code=status_code, detail=detail)
```

### Step 2: Add exception handler

```python
@app.exception_handler(ApiError)
async def api_error_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
```

### Step 3: Migrate key errors

Don't do all at once. Start with the most common:

```python
# Before
raise HTTPException(400, "text required")

# After
raise ApiError(400, "MISSING_FIELD", "text is required", field="text")
```

```python
# Before
raise HTTPException(404, "Room not found")

# After
raise ApiError(404, "NOT_FOUND", "Room not found")
```

Common error codes:
- `MISSING_FIELD` — required field not provided
- `INVALID_FIELD` — field value invalid
- `NOT_FOUND` — resource doesn't exist
- `FORBIDDEN` — not authorized
- `RATE_LIMITED` — too many requests
- `TOO_LARGE` — payload exceeds limit

### Step 4: Keep backward compatibility

The existing `{"detail": "..."}` format should still work for any errors not yet migrated. Don't break existing agent code.

## Verification

1. `make test-api` — update assertions to handle new error shape.
2. Trigger various errors via curl, verify structured JSON response.

## Notes

Migrate gradually. Don't try to convert every HTTPException at once. Start with errors agents are most likely to encounter: auth failures, missing fields, room not found.
