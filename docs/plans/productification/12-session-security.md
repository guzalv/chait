# 12 — Session Security (SameSite, Expiry, Logout)

**Severity**: high
**Area**: security
**Effort**: small

## Problem

Three session-related security issues:

1. `server.py:658` — session cookie has no `SameSite` attribute, enabling CSRF attacks.
2. `server.py:195` — `auth_human` never checks session age. Sessions valid forever server-side.
3. No logout endpoint — compromised sessions can't be invalidated.

## Implementation

### Fix 1: SameSite on cookie

At `server.py:658`, change:

```python
# Before
resp.set_cookie("chait_session", tok, httponly=True, max_age=86400 * 7)

# After
resp.set_cookie("chait_session", tok, httponly=True, samesite="lax", max_age=86400 * 7)
```

### Fix 2: Server-side session expiry

At `server.py:195`, change the session lookup query:

```python
# Before
rows = await db.execute_fetchall("SELECT * FROM sessions WHERE token = ?", (session_token,))

# After
from datetime import timedelta
cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
rows = await db.execute_fetchall(
    "SELECT * FROM sessions WHERE token = ? AND created_at > ?",
    (session_token, cutoff))
```

### Fix 3: Logout endpoint

Add a new endpoint after the login endpoint:

```python
@app.post("/logout")
async def logout(request: Request):
    session_token = request.cookies.get("chait_session")
    if session_token:
        db = await get_db()
        await db.execute("DELETE FROM sessions WHERE token = ?", (session_token,))
        await db.commit()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("chait_session")
    return resp
```

Add a logout button to the dashboard HTML header area.

## Verification

1. `make test-api` — existing tests pass.
2. New test: create session, wait (or manually backdate `created_at`), verify auth rejects expired session.
3. New test: POST `/logout`, verify session cookie is cleared and subsequent requests redirect to login.
4. Manual: verify cross-origin form POST to `/ui/api/rooms` is blocked by SameSite.
