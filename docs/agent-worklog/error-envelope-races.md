# Error Envelope Unification & Check-then-Insert Races

## Changes
- **Unified error envelope (`server.py`).** The docs (`INSTRUCTIONS`) promise every error is `{"error":{"code","message"}}`, but many endpoints raised raw `HTTPException` (FastAPI default `{"detail":...}`) and validation errors returned `{"detail":[...]}` (422). Replaced the single `@app.exception_handler(ApiError)` with a `StarletteHTTPException` handler that wraps ALL HTTPExceptions (ApiError is a subclass, so one handler covers both), plus a `RequestValidationError` handler. Both now emit the `{"error":{...}}` shape.
  - **Redirects preserved.** `require_human` raises `HTTPException(303, headers={"Location":"/login"})` to bounce unauthenticated humans to the login page (used by all `/ui/api/*`). The handler detects 3xx status OR a `Location` header and returns a bare `Response(status_code, headers)` — a real redirect, not JSON — so the human UI and UI test suite keep working.
  - **Status→code mapping** for raw HTTPExceptions: 400→`INVALID_REQUEST`, 401→`AUTH_REQUIRED`, 403→`FORBIDDEN`, 404→`NOT_FOUND`, 409→`CONFLICT`, 413→`TOO_LARGE`, 429→`RATE_LIMITED`, 503→`UNAVAILABLE`, else `ERROR`. `exc.headers` is preserved for non-redirect cases too.
  - **Validation errors** return 422 `{"error":{"code":"INVALID_FIELD","message":<first msg>,"field":<dotted loc>}}`.
  - **Emit documented codes.** Converted the raw HTTPExceptions where a documented code applies: `ui_create_room` "name required"→`MISSING_FIELD`(field=name); `ui_send_message`/`ui_send_dm` empty text→`MISSING_FIELD`(field=text) and over-length→`TOO_LARGE`. `MISSING_FIELD` and `TOO_LARGE` were previously documented but never emitted. The remaining raw 404s / get_db 503 stay raw — the generic handler now wraps them correctly.
- **Fixed check-then-insert races (`server.py`).** Both room-create and idempotent-join did `SELECT ... WHERE name=?` then a conditional `INSERT` with no atomicity; concurrent requests interleave.
  - **Agents:** added a unique index `idx_agents_name_room ON agents(name, room_id)` in the migrations section, after a dedupe (`DELETE ... WHERE rowid NOT IN (SELECT MIN(rowid) ... GROUP BY name, room_id)`) so legacy DBs with existing duplicates can still create the index. `join_with_token`'s new-agent `INSERT` is now wrapped in `try/except aiosqlite.IntegrityError`; on conflict it rolls back, re-`SELECT`s the existing agent by (name, room_id) and returns it (idempotent re-join) instead of 500.
  - **Rooms:** `rooms.name` is already UNIQUE, so a concurrent duplicate create previously 500'd on `IntegrityError`. Both `api_create_room` and `ui_create_room` now wrap the `INSERT` in `try/except aiosqlite.IntegrityError`; on conflict they roll back, re-`SELECT` the existing room and return it with `"existing": True` — identical shape to the check-then-return path.

## Decisions
- **One handler on `StarletteHTTPException` instead of keeping the `ApiError` handler.** `ApiError` → `fastapi.HTTPException` → `StarletteHTTPException`, and Starlette resolves handlers by walking the exception MRO, so a single base-class handler catches ApiError, raw HTTPExceptions, and FastAPI's internal ones — no conflict, no duplication.
- **Redirect detection uses status 3xx OR a `Location` header (case-insensitive).** Belt-and-suspenders: any future redirect raised as an HTTPException (regardless of exact status) stays a redirect rather than being JSON-ified.
- **Race tests are deterministic single-threaded** (per task): the join-twice / create-twice paths exercise the idempotency contract via the check-then-return branch. The new `try/except IntegrityError` branches are the safety net for true concurrency and return the *same* output as the tested branch, so a mock-based test of that branch would only test the mock. The unique index + dedupe are the real structural guarantee and are covered by the join-idempotency test asserting exactly one member row.
- **`db.rollback()` on IntegrityError** before the follow-up re-SELECT/UPDATE, to clear the aborted implicit transaction left by the failed INSERT.

## Testing
- `make test-api`: **82 passed** (76 existing + 6 new in `tests/test_api.py`):
  - `TestErrorEnvelope`: `test_missing_field_envelope` (400 → `MISSING_FIELD`, field=name), `test_not_found_envelope` (404 → `NOT_FOUND`), `test_validation_error_envelope` (limit=-1 → 422 `INVALID_FIELD`), `test_unauthenticated_ui_redirect_preserved` (unauth `/ui/api/*`, `follow_redirects=False` → 303 `Location:/login`, no JSON error body — critical regression guard).
  - `TestConcurrencyRaces`: `test_join_same_name_is_idempotent` (same id on re-join, exactly one member row), `test_duplicate_room_returns_existing_once` (`existing:True`, exactly one room).
- `make test-integration`: **17 passed**.
- `make test-ui`: **54 passed** — exercises the login/redirect flow the handler could have broken.
- `make lint`: `ruff check` + `ruff format --check` clean.
