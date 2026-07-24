# 42 — Enable Foreign Key Constraints

**Severity**: important
**Area**: data integrity
**Effort**: small

## Problem

`server.py:70-135` — no `FOREIGN KEY` constraints in schema. SQLite defaults to `PRAGMA foreign_keys = OFF`. This means:
- DMs to nonexistent `target_id` silently succeed and are never deliverable
- `reply_to` can reference nonexistent message IDs
- If rooms were ever deleted, orphaned messages/members/documents would remain

## Implementation

### Step 1: Enable foreign keys

In `init_db()`, after the connection is opened (after line 69), add:

```python
await _db.execute("PRAGMA foreign_keys = ON")
```

### Step 2: Add FK constraints to schema

This is tricky for an existing database — `ALTER TABLE` can't add foreign keys in SQLite. For new databases, update the `CREATE TABLE` statements:

```sql
CREATE TABLE IF NOT EXISTS room_members (
    room_id TEXT NOT NULL REFERENCES rooms(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    ...
)

CREATE TABLE IF NOT EXISTS messages (
    ...
    room_id TEXT NOT NULL REFERENCES rooms(id),
    author_id TEXT NOT NULL,
    ...
)

CREATE TABLE IF NOT EXISTS documents (
    ...
    room_id TEXT NOT NULL REFERENCES rooms(id),
    ...
)
```

For existing databases, the `CREATE TABLE IF NOT EXISTS` will not modify existing tables. FKs will only apply to new installations.

### Step 3: Verify no existing violations

Before enabling FK enforcement on existing DBs, check:

```sql
PRAGMA foreign_key_check;
```

If violations exist, clean them up (delete orphaned rows) or skip FK enforcement on existing data.

## Verification

1. `make test-api` — passes.
2. New test: try to insert a message with nonexistent `room_id` — should fail.
3. Existing data: run `PRAGMA foreign_key_check` on existing DB files.

## Notes

The FK enforcement (`PRAGMA foreign_keys = ON`) must be set per-connection — it's not persisted. It's set after every `aiosqlite.connect()`.

For the `dms` table, `from_id`/`to_id` referencing `agents(id)` is debatable — the human uses `from_id="human"` which doesn't exist in agents. Either use a special row in agents for the human or skip FKs on DMs.
