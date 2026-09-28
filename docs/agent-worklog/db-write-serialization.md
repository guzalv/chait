# DB Write Serialization

## Changes
- Introduced a module-level `asyncio.Lock` (`_write_lock`) next to the single shared `aiosqlite` connection (`_db`) in `server.py`.
- Wrapped every write sequence — from its first `INSERT`/`UPDATE`/`DELETE` through its `await db.commit()` — in `async with _write_lock:` across all 21 write handlers: `join_with_token` (both the existing-agent card update and the insert+IntegrityError fallback), `api_create_room`, `update_card`, `deregister`, `set_room_status`, `post_message`, `send_dm`, `upload_document`, `login_submit`, `logout`, `ui_create_room`, `ui_send_message`, `ui_upload_document`, `ui_send_dm`, `ui_set_room_status`, `ui_archive_room`, `ui_unarchive_room`, `ui_delete_room`, `ui_remove_agent`, `ui_create_api_token`, `ui_revoke_api_token`.

Why: the process uses ONE shared connection in sqlite's default implicit-transaction mode (`isolation_level=''`). Coroutines interleave at `await` points, and any `commit()` flushes ALL pending writes on the connection. So a request that returned/failed before its own `commit()` could have its partial writes committed by an unrelated concurrent `commit()`, and multi-statement operations (notably `ui_delete_room`'s 4 `DELETE`s + 1 `commit`) were not atomic. Serializing all writers behind one lock means only one writer runs at a time, so each `commit()` flushes only that writer's own writes and any multi-statement sequence held under the lock is atomic.

## Decisions
- Kept the single connection and implicit-transaction mode. Did NOT change `isolation_level`, switch to autocommit, or add explicit `BEGIN`/`COMMIT`. The lock plus the existing implicit transactions are sufficient and the smallest correct change.
- Critical sections are kept minimal: pre-write reads (`_require_room_member`, `_get_room_id`, existence checks, target-agent lookup) stay OUTSIDE the lock; on-disk file writes for uploads and `shutil.rmtree` cleanup for room deletion also stay outside. Only the actual write statements plus their `commit` are held.
- The three recent `IntegrityError` try/except fallbacks (`join_with_token`, `api_create_room`, `ui_create_room`) keep the whole write + `except` + re-SELECT + `commit` inside the lock, so the idempotent fallback stays consistent.
- `ui_delete_room` holds the lock across all four `DELETE`s and the `commit` — the key atomicity fix — with the filesystem `rmtree` left outside (after commit).
- `init_db()` is left unlocked: it runs at startup before any requests are served.
- Read-only handlers (list/get/unread/SSE/download) were not touched — they perform no writes; wrapping them would needlessly serialize reads and risk holding the lock across the long-poll/SSE waits.
- Test fixture resets `server._write_lock` per test. An `asyncio.Lock` binds to the first event loop that *contends* it, and each `TestClient` runs in its own portal loop; a fresh unbound lock per test keeps the suite loop-safe (matches the fixture's existing reset of `_db`, `_unread_events`, `_ui_subscribers`, `_rate_buckets`).

## Testing
- `make test-api` — 84 passed (2 new).
  - `TestWriteSerialization::test_concurrent_message_posts_all_persist_once`: fires 20 concurrent posts at one room through the TestClient portal loop (`asyncio.to_thread` + `asyncio.gather`) so they genuinely contend `_write_lock`; asserts all 20 return 200 and every message body persists exactly once (final set == the 20 sent). A lost commit or a deadlock would fail this.
  - `TestWriteSerialization::test_write_lock_is_asyncio_lock`: asserts the invariant object exists and is an `asyncio.Lock`.
  - Existing `TestRooms::test_delete_room_removes_it_and_its_data` is the atomicity regression guard for `ui_delete_room` (asserts a deleted room leaves no rooms/agents/messages/documents/on-disk dir).
- `make test-integration` — 17 passed.
- `make test-ui` — 54 passed.
- `make lint` — ruff check + format check clean.
