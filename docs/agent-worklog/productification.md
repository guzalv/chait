# Productification

## Changes
- Implemented 40 of 43 productification plans across 6 waves.
- Wave 1 (01-07): WAL mode, Docker binding, upload security, error handling, dep cleanup, query limits, message limits.
- Wave 2 (08-13): Room membership auth, document download auth, XSS prevention, role restrictions, session security, DM validation.
- Wave 3 (14-21): Extracted HTML templates, added logging, health endpoint, Dockerfile hardening, Docker examples, default password guard.
- Wave 4 (22-28): DM conversation view, unread indicators, connection status, human/agent styling, room status control, modal keyboard shortcuts, UI polish bundle.
- Wave 5 (29-38): Room lookup helpers, API instructions overhaul, README fixes, consistent response shapes, DB index, idempotent join.
- Wave 6 (39-43): Rate limiting, async file I/O, thundering herd jitter, foreign keys, agent token expiry/revocation.

## Decisions
- Plans 33 (structured errors), 36 (Pydantic models), 37 (response envelope) deferred: they add complexity and breaking changes with limited immediate benefit for an MVP.
- Default password guard uses auto-generate-and-log approach (in `__main__`) rather than hard-fail, to preserve zero-config dev experience while warning operators.
- Rate limiter is in-memory (resets on restart). Sufficient for MVP; persistent rate limiting adds write contention.
- Foreign key enforcement is per-connection via PRAGMA, applied to new data only.

## Testing
- All 66 existing tests pass after every wave (50 API + 16 integration).
- Rate bucket state cleared between tests to prevent cross-test interference.
