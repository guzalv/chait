# Monotonic Timestamps

## Changes
- Rewrote `_now()` (server.py) to return strictly increasing, unique ISO timestamps for the process lifetime. It now emits fixed 6-digit microseconds (`isoformat(timespec="microseconds")`) and, when the wall clock ties with or regresses behind the last emitted value, bumps 1µs past the previous value (`datetime.fromisoformat(_last_ts) + timedelta(microseconds=1)`). A module-level `_last_ts` holds the last value.
- Seeded `_last_ts` in `init_db()` (after schema/migrations, before commit) from `MAX(created_at)` across `messages`, `dms`, `documents`, `rooms`, and `agents`, so timestamps keep increasing across restarts instead of re-emitting values <= persisted rows.
- Added tests in `tests/test_api.py` (`TestMonotonicNow`): strictly-increasing + unique over 1000 calls, same-instant microsecond bump with a frozen clock, and a cursor no-loss regression that paginates `limit=1` while advancing `since` and asserts every posted message is retrieved.

## Decisions
- **Root cause:** pagination uses `created_at > since` (get_messages, get_dms, the 3-stream unread long-poll) and clients advance `since` to the newest `created_at` seen. The old `_now()` used bare `datetime.now(...).isoformat()` at microsecond resolution; two rows in the same microsecond share a timestamp, so advancing past the first with strict `>` permanently skips the second (silent message loss). A backward wall-clock step (NTP) could also drop/reorder rows.
- **Chosen fix: monotonic `_now()`, not a rowid/integer wire cursor.** The app is single-process (in-memory `_write_lock`, `_rate_buckets`, `_unread_events`, `_ui_subscribers`) and DB writes are serialized behind `_write_lock`. Making the process timestamp source strictly increasing and unique eliminates both same-microsecond ties and backward clock steps, fixing messages, DMs, AND the unread cursor at once — with no change to the API, clients, or docs. Switching the wire cursor to integer rowids would fix the same defect but require coordinated API/client/doc changes across three streams and is far higher risk.
- **No lock added.** The `_now()` read-modify-write of `_last_ts` is purely synchronous (no `await`), so the GIL makes it atomic across coroutines; adding a lock would be redundant.
- **Format kept lexicographic.** Always emitting 6 microsecond digits keeps the string format fixed so lexicographic order == chronological order, matching the string comparison the cursor already uses.

## Testing
- `make test-api`: 87 passed.
- `make test-integration`: 17 passed.
- `make test-ui`: 54 passed.
- `make lint`: ruff check + format check both clean.
- Verified the cursor no-loss regression fails against the pre-fix behavior conceptually (same-microsecond ties) and passes with distinct monotonic timestamps. No existing test assumed the old bare-`isoformat()` format; the API round-trips timestamps as opaque strings, so the added microsecond padding is transparent to callers.
