# Disable auth for local dev (CHAIT_DISABLE_AUTH)

## Changes
- Added `CHAIT_DISABLE_AUTH` env var: when set (`1`/`true`/`yes`), `/login` (GET and POST) skips the credential form entirely and auto-issues a session cookie via a new shared `_issue_session()` helper, so the dashboard is reachable with zero setup.
- `_ensure_human_password()` (startup guard) skips password generation/warning when auth is disabled, since there's no password to protect.
- `hack/dev-server.sh` now launches with `CHAIT_DISABLE_AUTH=1` instead of generating a random per-run password — matches how the script is actually used (quick local iteration).
- README documents the flag, reorders the Quick start section to lead with the no-login path, and adds it to the env var table.

## Decisions
- Picked up as an interrupted agent session (found as uncommitted working-tree changes with no branch/PR). Rebased onto latest `main` rather than the stale base branch it started from (`docs/task-specific-guidance`, itself already merged and far behind).
- `_issue_session()` extracted from the existing login-success code path rather than duplicated, so the disabled-auth path gets the same hashed-token-at-rest, write-lock, and `SECURE_COOKIES` handling as normal login (those hardening features landed on `main` after this session started; extracting the helper kept both paths in sync instead of letting the new path skip them).
- `/` (dashboard) needed no changes: it already redirects to `/login` when there's no session, and `/login` now short-circuits to a session when disabled — one extra redirect hop on first load, no behavior change otherwise.
- Encountered and discarded an unrelated, already-upstream change (PR #33, "steer agents to a zero-token idle poll") that surfaced as a stray unstaged diff in `server.py` mid-rebase; resolved by taking `main`'s version as-is rather than merging a stale local copy of it.
- Separately reverted an AGENTS.md/`.gitignore` policy change (PR #21's "worklogs are local-only") per explicit user request — landed first as its own PR (#32) since it's unrelated to this feature.

## Testing
- `make test-api`: 97 passed (added `TestAuthDisabled`: startup password guard skipped, `/login` GET+POST issue a session unconditionally, `/` reachable with no prior cookie, and confirmed normal login is still enforced when the flag is unset).
- `make test-integration`: 17 passed (unaffected).
- `make test-ui`: 56 passed (unaffected; UI tests run against normal auth).
- `make lint`: clean.
- Manual: ran `hack/dev-server.sh`, confirmed `GET /` with no cookies returns 200 with the dashboard and a `chait_session` cookie, and the log prints the "UNAUTHENTICATED" warning.
