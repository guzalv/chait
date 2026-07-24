# 01 — Enable SQLite WAL Mode and Busy Timeout

**Severity**: critical
**Area**: performance
**Effort**: tiny (2 lines)

## Problem

`server.py:68` opens the SQLite connection with default journal mode (rollback). In this mode, readers block writers and writers block readers. Under concurrent agent load, writes fail immediately with `SQLITE_BUSY` (default busy timeout is 0).

## Implementation

In `server.py`, in `init_db()`, after line 69 (`db.row_factory = aiosqlite.Row`), add:

```python
await _db.execute("PRAGMA journal_mode=WAL")
await _db.execute("PRAGMA busy_timeout=5000")
```

WAL allows concurrent reads during writes. Busy timeout retries for 5 seconds instead of failing immediately.

## Verification

1. `make test-api && make test-integration` — existing tests pass.
2. Manual: start server, use two terminals to simultaneously post messages and poll. No `database is locked` errors.
3. Check: `sqlite3 data/chait.db "PRAGMA journal_mode"` should return `wal`.
