# Backend Hardening (Small Fixes)

## Changes
- **Bounded `limit` query param** on `GET /api/v1/rooms/{room}/messages` and `GET /api/v1/dm/{target_id}` (`server.py`). Both used `Query(default=50, le=200)` with no lower bound, so `limit=-1` reached SQLite's `LIMIT -1` (interpreted as "no limit" → unbounded fetch) and `limit=0` returned nothing. Added `ge=1` so both are now rejected with `422`.
- **Validated `PUT /api/v1/me/card`.** The handler read `card = await request.json()`, which raised an unhandled `500` on malformed JSON and accepted non-object bodies (list/number), violating the "card is an object" contract. Changed the signature to accept `card: dict` as the body parameter so FastAPI validates it (→ `422` for bad input) while keeping the existing behavior (`json.dumps(card)` storage, `{"updated": True, "card": card}` response). No size cap / rate limit added here (deferred to a separate PR).
- **Constant-time credential check** in `login_submit` (`server.py`). Replaced the `==` comparisons with `secrets.compare_digest(str(form.get(...) or ""), ...)` for both user and password, removing a timing side channel on the human login. Success/failure behavior is unchanged.
- **Added `idx_documents_room` index** on `documents(room_id, created_at)` in the `init_db` schema block. `documents` is queried by `room_id` in several places (list/download authz) but had no index; it's created idempotently alongside the existing indexes and via `CREATE INDEX IF NOT EXISTS` so existing DBs pick it up on startup.
- **Evict stale rate-limit buckets.** `_rate_buckets` grew unbounded — one entry per distinct key (e.g. per login source IP) was never removed once empty. Added a module-level `_last_sweep` and `_sweep_rate_buckets(now)`, called from `_check_rate`, that at most once every 60s trims each bucket to entries newer than 60s and deletes now-empty buckets. External limiter behavior is unchanged.
- **Bounded the SSE broadcast queues.** `ui_events` now creates `asyncio.Queue(maxsize=100)` per connected dashboard, and `_broadcast_ui` wraps `put_nowait` in `try/except asyncio.QueueFull`, dropping the tick on a full queue. A slow/stalled browser connection could otherwise back an unbounded queue and leak memory. Dropping is safe: the payload is only a debug label and every browser re-runs its idempotent fetches on any event, so a full queue already has a pending tick that triggers the same refresh (comment added inline).

## Decisions
- `update_card` takes `card: dict` rather than a Pydantic model: it preserves the arbitrary-object contract (any JSON object) with the minimum change, and FastAPI still rejects non-objects and malformed JSON with `422`.
- Rate-bucket sweep is a simple O(n) pass gated by a 60s interval rather than a background task or per-key TTL — cheapest thing that bounds growth without changing the limiter's semantics or adding a scheduler.
- SSE overflow drops the newest event instead of disconnecting the client or blocking the producer: broadcasts are fire-and-forget and idempotent, so a dropped tick is self-healing.
- `idx_documents_room` uses `(room_id, created_at)` to mirror the existing `messages`/`dms` indexes and cover the room-scoped list ordering.

## Testing
- `make test-api`: 68 passed. Added to `tests/test_api.py`:
  - `TestMessages::test_get_messages_negative_limit_returns_422` and `TestDMs::test_get_dms_negative_limit_returns_422` (`limit=-1` and `limit=0` → 422).
  - `TestAgentCards::test_update_card_rejects_non_dict_body` (`[1,2,3]` → 422) and `test_update_card_rejects_malformed_json` (invalid JSON → 422, not 500).
  - `TestRateLimit::test_message_rate_limit_returns_429`: exceeds the 30/min message cap for one agent and asserts `429` with error code `RATE_LIMITED`. Clears the module-global `_rate_buckets` at start and end since it is shared across tests (matching the `client` fixture, which also clears it).
- `make test-integration`: 17 passed.
- `make lint`: `ruff check` + `ruff format --check` clean (ran `make fmt` on the new test).
- `make test-ui`: 54 passed (unaffected by these changes; run as a sanity check).
