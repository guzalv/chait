# Security Hardening

## Changes
- **Conditional Secure cookie flag.** Added `SECURE_COOKIES` config
  (`CHAIT_SECURE_COOKIES`, default `false`) and passed `secure=SECURE_COOKIES`
  to the `chait_session` cookie in `login_submit`. The app serves plain HTTP by
  default (local dev), so `Secure` can't be forced unconditionally; deployments
  behind TLS enable it via env var. The flag is read as a module global at call
  time so it can be monkeypatched.
- **Admin-password guard for all entrypoints.** Moved the "no default password"
  logic out of the `if __name__ == "__main__"` block (which only runs for
  `python server.py`) into a new `_ensure_human_password()` helper called from
  the `lifespan` startup, so it also protects `uvicorn server:app` / gunicorn.
  If `HUMAN_PASS` is empty or the literal default `changeme`, it generates
  `secrets.token_urlsafe(16)`, assigns the module global, and logs a WARNING.
- **Reject oversized bodies early.** Added an `@app.middleware("http")` that
  returns `413` immediately (before `call_next`, so the body is never
  parsed/spooled) when `Content-Length` exceeds `MAX_UPLOAD_BYTES + 1_000_000`
  (~1 MB multipart overhead). Chunked requests (no `Content-Length`) fall
  through to the in-handler caps. The middleware reads `MAX_UPLOAD_BYTES` at
  request time (monkeypatchable) and returns the project's structured envelope
  `{"error": {"code": "TOO_LARGE", "message": ...}}`.
- **Consistent envelope on upload caps.** Converted the two in-handler upload
  413s from raw `HTTPException(413, ...)` to
  `ApiError(413, "TOO_LARGE", ...)` in `upload_document` and
  `ui_upload_document`. Other raw `HTTPException`s were left untouched (a
  separate PR handles the general error-envelope cleanup).
- **Docs.** Added a `CHAIT_SECURE_COOKIES` row to the README Configuration
  table and a sentence directing non-local deployments to run behind a
  TLS-terminating proxy with `CHAIT_SECURE_COOKIES=true` and `CHAIT_HUMAN_PASS`.
- **Test helpers.** `_login` in `test_api.py` and `test_integration.py` now read
  `server.HUMAN_PASS` dynamically instead of hard-coding `changeme`, because
  startup now regenerates the default password.

## Decisions
- **Log the generated DEV password.** `_ensure_human_password()` logs the
  random password it generates. This is a deliberate local-dev convenience so
  an operator who forgot to set `CHAIT_HUMAN_PASS` can still log in. An
  operator-provided password is never logged. Production is expected to set
  `CHAIT_HUMAN_PASS`, in which case nothing is generated or logged.
- **Content-Length check, not body streaming.** The point is to fail fast
  before RAM/temp-disk is consumed; inspecting `Content-Length` in middleware
  is the minimal way to do that. Chunked uploads (no header) are rare here and
  still bounded by the existing in-handler `read(MAX+1)` caps, so they proceed.
- **Middleware ceiling = cap + 1 MB.** Multipart form uploads add framing
  overhead on top of the raw file bytes; the +1 MB slack avoids rejecting a
  legitimately max-sized file whose multipart envelope pushes it just over.
- **Dynamic `_login` password over per-test env fixtures.** Reading
  `server.HUMAN_PASS` at login time is a one-line change that keeps every
  existing test working after startup regenerates the default, without touching
  fixtures across two files.

## Testing
- `make test-api`: 76 passed (6 new in `TestSecurityHardening`: Secure cookie
  on/off, `_ensure_human_password` replace-default / keep-custom, oversized-body
  413 + normal-request regression).
- `make test-integration`: 17 passed.
- `make test-ui`: 54 passed (UI sets a non-default `CHAIT_HUMAN_PASS`, so the
  startup guard leaves it untouched and login still works).
- `make lint`: ruff check + format clean.
