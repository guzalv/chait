# UI send data loss

## Changes
- Fixed silent data loss in `sendMessage()` and `sendDM()` in `templates/dashboard.html`. Both used to `await api(...)` and then unconditionally clear the input. Since `api()` returns `null` on any failure (HTTP 413 "message too long", other non-OK, network error) and only logs to console, a failed send wiped the user's typed text with no feedback — and the user believed it had sent.
- Now both capture the result (`const res = await api(...)`) and on failure (`!res`) preserve the input and show a brief inline error, returning early. The input is only cleared and the view refreshed on success.
- Added a small `sendError(inputId)` helper that inserts (once) a red inline error line right after the given input and auto-clears it after 5s. Reuses the existing `#ef4444` red already used elsewhere in the file.

## Decisions
- Inline error line over a toast/modal: the task asked for minimal, non-intrusive feedback with no new frameworks/modals. Injecting one `<div>` after the input keeps the diff to a handful of lines and needs no HTML/CSS changes.
- `!res` is a safe success/failure signal: both UI endpoints (`ui_send_message`, `ui_send_dm` in `server.py`) return a non-empty JSON object on success, so `!res` is only true on failure — the success path is unchanged.
- Did not touch `api()`'s signature or other callers (e.g. `uploadFile()`), staying within scope.

## Testing
- `make test-ui`: 54 passed (headless chromium via snap chromedriver). Existing send/DM tests still pass.
- `make test-api`: 63 passed.
- `make lint`: ruff check + format check both clean.
