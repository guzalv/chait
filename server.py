"""chait - real-time AI agent collaboration chat server."""

import asyncio
import json
import logging
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
logger = logging.getLogger("chait")

_TEMPLATE_DIR = Path(__file__).parent / "templates"
DATA_DIR = Path(os.getenv("CHAIT_DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "chait.db"
DOCS_DIR = DATA_DIR / "documents"
PORT = int(os.getenv("CHAIT_PORT", "3100"))
HUMAN_USER = os.getenv("CHAIT_HUMAN_USER", "admin")
HUMAN_PASS = os.getenv("CHAIT_HUMAN_PASS", "changeme")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_MESSAGE_LENGTH = 100_000  # 100 KB
RESERVED_ROLES = {"god", "human", "admin", "system"}

# Long-poll: new-message event per agent
_unread_events: dict[str, asyncio.Event] = {}


def _notify_agent(agent_id: str):
    ev = _unread_events.get(agent_id)
    if ev:
        ev.set()


def _notify_room_members(db_rows, exclude: str = ""):
    for r in db_rows:
        aid = dict(r)["agent_id"]
        if aid != exclude:
            _notify_agent(aid)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise HTTPException(503, "Database unavailable")
    return _db


async def init_db():
    global _db
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    logger.info("Database initialized at %s", DB_PATH)
    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'agent',
            token TEXT NOT NULL UNIQUE,
            card TEXT DEFAULT '{}',
            room_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            topic TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            join_token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS room_members (
            room_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (room_id, agent_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_role TEXT NOT NULL DEFAULT 'agent',
            text TEXT NOT NULL,
            reply_to TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dms (
            id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL,
            from_name TEXT NOT NULL,
            to_id TEXT NOT NULL,
            text TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT DEFAULT 'application/octet-stream',
            uploaded_by TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
            id TEXT PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_dms_to ON dms(to_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_dms_from ON dms(from_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_room_members_agent ON room_members(agent_id, room_id);
    """)
    # Migrations for existing DBs
    for col, tbl, default in [
        ("card", "agents", "'{}'"),
        ("room_id", "agents", "NULL"),
        ("status", "rooms", "'active'"),
        ("join_token", "rooms", "''"),
    ]:
        try:
            await _db.execute(f"SELECT {col} FROM {tbl} LIMIT 1")
        except Exception:
            await _db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT DEFAULT {default}")
    await _db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


async def _get_agent_by_token(token: str) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM agents WHERE token = ?", (token,))
    return dict(rows[0]) if rows else None


async def auth_agent(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    agent = await _get_agent_by_token(auth[7:])
    if not agent:
        logger.warning("Auth failed: invalid token from %s", request.client.host if request.client else "unknown")
        raise HTTPException(401, "Invalid token")
    return agent


async def auth_master_token(request: Request) -> str:
    """Validate Bearer token against api_tokens table."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = auth[7:]
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM api_tokens WHERE token = ?", (token,))
    if not rows:
        raise HTTPException(401, "Invalid API token")
    return token


async def auth_human(request: Request) -> Optional[str]:
    session_token = request.cookies.get("chait_session")
    if not session_token:
        return None
    db = await get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = await db.execute_fetchall(
        "SELECT * FROM sessions WHERE token = ? AND created_at > ?", (session_token, cutoff)
    )
    return session_token if rows else None


async def auth_any(request: Request) -> dict:
    """Accept either agent bearer token or human session cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return await auth_agent(request)
    session_token = request.cookies.get("chait_session")
    if session_token:
        result = await auth_human(request)
        if result:
            return {"type": "human"}
    raise HTTPException(401, "Authentication required")


async def _get_room_id(db: aiosqlite.Connection, room_name: str) -> str:
    """Resolve room name to ID. Raises 404 if not found."""
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    return dict(rows[0])["id"]


async def _require_room_member(db: aiosqlite.Connection, room_name: str, agent_id: str) -> str:
    """Resolve room name to ID and verify agent membership. Returns room_id."""
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room_id = dict(rows[0])["id"]
    member = await db.execute_fetchall(
        "SELECT 1 FROM room_members WHERE room_id = ? AND agent_id = ?", (room_id, agent_id)
    )
    if not member:
        raise HTTPException(403, "Not a member of this room")
    return room_id


async def require_human(request: Request) -> str:
    token = await auth_human(request)
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return token


def _msg_dict(m) -> dict:
    d = dict(m)
    return {
        "id": d["id"],
        "author_id": d["author_id"],
        "author_name": d["author_name"],
        "author_role": d["author_role"],
        "text": d["text"],
        "reply_to": d.get("reply_to"),
        "priority": bool(d["priority"]),
        "created_at": d["created_at"],
    }


def _dm_dict(d) -> dict:
    r = dict(d)
    return {
        "id": r["id"],
        "from_id": r["from_id"],
        "from_name": r["from_name"],
        "to_id": r["to_id"],
        "text": r["text"],
        "priority": bool(r["priority"]),
        "created_at": r["created_at"],
    }


def _parse_card(raw) -> dict:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    if _db:
        await _db.close()


app = FastAPI(title="chait", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    try:
        db = await get_db()
        await db.execute_fetchall("SELECT 1")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")


# ---------------------------------------------------------------------------
# Instructions endpoint
# ---------------------------------------------------------------------------
INSTRUCTIONS = """# chait API

You are connected to a chait collaboration server.

**Base URL**: {base_url}/api/v1
**Auth**: `Authorization: Bearer <token>` header on every request.

## Registration

Join a room using your join token:

    POST /api/v1/join
    Content-Type: application/json
    {{"join_token": "<your-join-token>", "name": "Your Name", "role": "coder",
      "card": {{"description": "What you do", "skills": ["python", "testing"]}}}}

    Response: {{"id": "...", "token": "sk-...", "room": "room-name",
               "context": {{"topic": "...", "documents": [...]}}}}

Use the returned `token` as `Authorization: Bearer sk-...` for all subsequent requests.

## Endpoints

### Messages
- `POST /api/v1/rooms/{{room}}/messages` — Send message. Body: `{{"text": "...", "reply_to": "msg-id"}}`
- `GET  /api/v1/rooms/{{room}}/messages?since=<iso_timestamp>&limit=50`

### Rooms
- `GET  /api/v1/rooms` — List rooms you're in
- `GET  /api/v1/rooms/{{room}}` — Room details + members + their cards
- `POST /api/v1/rooms/{{room}}/status` — Set room status. Body: `{{"status": "active|waiting-for-input|completed|blocked"}}`

### Documents
- `POST /api/v1/rooms/{{room}}/documents` — Upload file (multipart, field: `file`, max 50 MB)
- `GET  /api/v1/rooms/{{room}}/documents` — List documents
- `GET  /api/v1/documents/{{doc_id}}/download` — Download (requires auth)

### Direct Messages
- `POST /api/v1/dm/{{agent_id}}` — Body: `{{"text": "..."}}`
- `GET  /api/v1/dm/{{agent_id}}?since=<iso_timestamp>`

### Identity
- `GET  /api/v1/me` — Your identity + card
- `PUT  /api/v1/me/card` — Update your card. Body: `{{"description": "...", "skills": [...]}}`

### Status (long-polling)
- `GET /api/v1/me/unread?wait=60&since=<iso_timestamp>` — **Long-poll**: blocks up to `wait` seconds until new messages arrive. Returns immediately if messages exist. Call this in a loop.

## Behavior
- Call `GET /api/v1/me/unread?wait=60` in a loop to stay responsive.
- Messages from humans have `priority: true` — address those first.
- Upload documents to share progress/artifacts with the room.
- Use DMs for private coordination.
- Update room status when the task state changes.

## Rate Limits
- Messages: 30 per minute per agent
- DMs: 20 per minute per agent
- File uploads: 10 per minute per agent
- Exceeding limits returns HTTP 429.
"""


@app.get("/api/v1/instructions")
async def get_instructions(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return Response(content=INSTRUCTIONS.format(base_url=base_url), media_type="text/markdown")


# ---------------------------------------------------------------------------
# Join: the only way agents get tokens
# ---------------------------------------------------------------------------
@app.post("/api/v1/join")
async def join_with_token(request: Request):
    """Agent joins a room using a join token. Returns agent auth token."""
    body = await request.json()
    join_token = body.get("join_token", "")
    name = body.get("name", "")
    role = body.get("role", "agent")
    card = body.get("card", {})
    if not join_token:
        raise HTTPException(400, "join_token required")
    if not name:
        raise HTTPException(400, "name required")
    if role in RESERVED_ROLES:
        raise HTTPException(400, f"role '{role}' is reserved")
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM rooms WHERE join_token = ?", (join_token,))
    if not rows:
        raise HTTPException(403, "Invalid join token")
    room = dict(rows[0])
    # Idempotent join: reuse existing agent if same name in same room
    existing = await db.execute_fetchall(
        "SELECT a.id, a.token, a.role FROM agents a JOIN room_members rm ON a.id = rm.agent_id "
        "WHERE a.name = ? AND rm.room_id = ?", (name, room["id"]))
    if existing:
        agent = dict(existing[0])
        if card:
            await db.execute("UPDATE agents SET card = ? WHERE id = ?", (json.dumps(card), agent["id"]))
            await db.commit()
        agent_id = agent["id"]
        agent_token = agent["token"]
        logger.info("Agent '%s' re-joined room '%s' (existing)", name, room["name"])
    else:
        agent_id = _uid()
        agent_token = f"sk-{secrets.token_hex(24)}"
        await db.execute(
            "INSERT INTO agents (id, name, role, token, card, room_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (agent_id, name, role, agent_token, json.dumps(card), room["id"], _now()),
        )
        await db.execute(
            "INSERT OR IGNORE INTO room_members (room_id, agent_id, joined_at) VALUES (?, ?, ?)",
            (room["id"], agent_id, _now()),
        )
        await db.commit()
        logger.info("Agent '%s' (role=%s) joined room '%s'", name, role, room["name"])
    # Include room context so agents know what they're joining
    docs = await db.execute_fetchall(
        "SELECT id, filename, size, created_at FROM documents WHERE room_id = ? ORDER BY created_at",
        (room["id"],),
    )
    return {
        "id": agent_id,
        "name": name,
        "role": role,
        "token": agent_token,
        "room": room["name"],
        "card": card,
        "context": {
            "topic": room.get("topic", ""),
            "documents": [dict(d) for d in docs],
        },
    }


# ---------------------------------------------------------------------------
# Room creation via master token
# ---------------------------------------------------------------------------
@app.post("/api/v1/rooms")
async def api_create_room(request: Request, _token: str = Depends(auth_master_token)):
    """Create a room using an API token. Returns room info + join_token."""
    body = await request.json()
    name = body.get("name", "")
    topic = body.get("topic", "")
    if not name:
        raise HTTPException(400, "name required")
    db = await get_db()
    existing = await db.execute_fetchall("SELECT id, join_token FROM rooms WHERE name = ?", (name,))
    if existing:
        return {
            "id": dict(existing[0])["id"],
            "name": name,
            "join_token": dict(existing[0])["join_token"],
            "existing": True,
        }
    room_id = _uid()
    join_token = f"chait-{secrets.token_hex(16)}"
    await db.execute(
        "INSERT INTO rooms (id, name, topic, status, join_token, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
        (room_id, name, topic, join_token, _now()),
    )
    await db.commit()
    logger.info("Room '%s' created (id=%s)", name, room_id)
    return {"id": room_id, "name": name, "topic": topic, "status": "active", "join_token": join_token}


# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------
@app.get("/api/v1/me")
async def me_endpoint(agent: dict = Depends(auth_agent)):
    return {"id": agent["id"], "name": agent["name"], "role": agent["role"], "card": _parse_card(agent.get("card"))}


@app.put("/api/v1/me/card")
async def update_card(request: Request, agent: dict = Depends(auth_agent)):
    card = await request.json()
    db = await get_db()
    await db.execute("UPDATE agents SET card = ? WHERE id = ?", (json.dumps(card), agent["id"]))
    await db.commit()
    return {"updated": True, "card": card}


# ---------------------------------------------------------------------------
# Room management
# ---------------------------------------------------------------------------
VALID_ROOM_STATUSES = {"active", "waiting-for-input", "completed", "blocked"}


@app.get("/api/v1/rooms")
async def list_rooms(agent: dict = Depends(auth_agent)):
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT r.id, r.name, r.topic, r.status, r.created_at FROM rooms r JOIN room_members rm ON r.id = rm.room_id WHERE rm.agent_id = ?",
        (agent["id"],),
    )
    return [dict(r) for r in rows]


@app.get("/api/v1/rooms/{room_name}")
async def get_room(room_name: str, agent: dict = Depends(auth_agent)):
    db = await get_db()
    room_id = await _require_room_member(db, room_name, agent["id"])
    rows = await db.execute_fetchall("SELECT * FROM rooms WHERE id = ?", (room_id,))
    room = dict(rows[0])
    room.pop("join_token", None)  # never expose join_token to agents
    members = await db.execute_fetchall(
        "SELECT a.id, a.name, a.role, a.card FROM agents a JOIN room_members rm ON a.id = rm.agent_id WHERE rm.room_id = ?",
        (room_id,),
    )
    room["members"] = [
        {
            "id": dict(m)["id"],
            "name": dict(m)["name"],
            "role": dict(m)["role"],
            "card": _parse_card(dict(m).get("card")),
        }
        for m in members
    ]
    return room


@app.post("/api/v1/rooms/{room_name}/status")
async def set_room_status(room_name: str, request: Request, agent: dict = Depends(auth_agent)):
    body = await request.json()
    new_status = body.get("status", "")
    if new_status not in VALID_ROOM_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(VALID_ROOM_STATUSES)}")
    db = await get_db()
    room_id = await _require_room_member(db, room_name, agent["id"])
    await db.execute("UPDATE rooms SET status = ? WHERE id = ?", (new_status, room_id))
    await db.commit()
    return {"room": room_name, "status": new_status}


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
@app.post("/api/v1/rooms/{room_name}/messages")
async def post_message(room_name: str, request: Request, agent: dict = Depends(auth_agent)):
    body = await request.json()
    text = body.get("text", "")
    reply_to = body.get("reply_to")
    if not text:
        raise HTTPException(400, "text required")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise HTTPException(413, f"Message too long ({MAX_MESSAGE_LENGTH} chars max)")
    db = await get_db()
    room_id = await _require_room_member(db, room_name, agent["id"])
    msg_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO messages (id, room_id, author_id, author_name, author_role, text, reply_to, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, room_id, agent["id"], agent["name"], agent["role"], text, reply_to, 0, now),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members, exclude=agent["id"])
    return {
        "id": msg_id, "room": room_name, "author_id": agent["id"],
        "author_name": agent["name"], "author_role": agent["role"],
        "text": text, "reply_to": reply_to, "priority": False, "created_at": now,
    }


@app.get("/api/v1/rooms/{room_name}/messages")
async def get_messages(
    room_name: str,
    since: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    agent: dict = Depends(auth_agent),
):
    db = await get_db()
    room_id = await _require_room_member(db, room_name, agent["id"])
    if since:
        msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE room_id = ? AND created_at > ? ORDER BY created_at LIMIT ?",
            (room_id, since, limit),
        )
    else:
        msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE room_id = ? ORDER BY created_at DESC LIMIT ?",
            (room_id, limit),
        )
        msgs = list(reversed(msgs))
    return [_msg_dict(m) for m in msgs]


# ---------------------------------------------------------------------------
# DMs
# ---------------------------------------------------------------------------
@app.post("/api/v1/dm/{target_id}")
async def send_dm(target_id: str, request: Request, agent: dict = Depends(auth_agent)):
    body = await request.json()
    text = body.get("text", "")
    if not text:
        raise HTTPException(400, "text required")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise HTTPException(413, f"Message too long ({MAX_MESSAGE_LENGTH} chars max)")
    db = await get_db()
    target = await db.execute_fetchall("SELECT id FROM agents WHERE id = ?", (target_id,))
    if not target:
        raise HTTPException(404, "Target agent not found")
    dm_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO dms (id, from_id, from_name, to_id, text, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (dm_id, agent["id"], agent["name"], target_id, text, 0, now),
    )
    await db.commit()
    _notify_agent(target_id)
    return {
        "id": dm_id, "from_id": agent["id"], "from_name": agent["name"],
        "to_id": target_id, "text": text, "priority": False, "created_at": now,
    }


@app.get("/api/v1/dm/{target_id}")
async def get_dms(
    target_id: str,
    since: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    agent: dict = Depends(auth_agent),
):
    db = await get_db()
    if since:
        rows = await db.execute_fetchall(
            "SELECT * FROM dms WHERE ((from_id=? AND to_id=?) OR (from_id=? AND to_id=?)) AND created_at > ? ORDER BY created_at LIMIT ?",
            (agent["id"], target_id, target_id, agent["id"], since, limit),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM dms WHERE ((from_id=? AND to_id=?) OR (from_id=? AND to_id=?)) ORDER BY created_at DESC LIMIT ?",
            (agent["id"], target_id, target_id, agent["id"], limit),
        )
        rows = list(reversed(rows))
    return [_dm_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Unread with long-polling
# ---------------------------------------------------------------------------
@app.get("/api/v1/me/unread")
async def unread(
    agent: dict = Depends(auth_agent),
    since: Optional[str] = None,
    wait: int = Query(default=0, le=120),
):
    db = await get_db()
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    async def _fetch():
        room_msgs = await db.execute_fetchall(
            "SELECT m.*, r.name as room_name FROM messages m JOIN rooms r ON m.room_id = r.id JOIN room_members rm ON m.room_id = rm.room_id WHERE rm.agent_id = ? AND m.created_at > ? AND m.author_id != ? ORDER BY m.created_at",
            (agent["id"], since, agent["id"]),
        )
        dm_msgs = await db.execute_fetchall(
            "SELECT * FROM dms WHERE to_id = ? AND created_at > ? ORDER BY created_at",
            (agent["id"], since),
        )
        new_docs = await db.execute_fetchall(
            "SELECT d.*, r.name as room_name FROM documents d JOIN rooms r ON d.room_id = r.id JOIN room_members rm ON d.room_id = rm.room_id WHERE rm.agent_id = ? AND d.created_at > ? AND d.uploaded_by != ? ORDER BY d.created_at",
            (agent["id"], since, agent["id"]),
        )
        return room_msgs, dm_msgs, new_docs

    room_msgs, dm_msgs, new_docs = await _fetch()

    if not room_msgs and not dm_msgs and not new_docs and wait > 0:
        ev = asyncio.Event()
        _unread_events[agent["id"]] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        finally:
            _unread_events.pop(agent["id"], None)
        room_msgs, dm_msgs, new_docs = await _fetch()

    return {
        "room_messages": [
            {
                "id": dict(m)["id"],
                "room": dict(m)["room_name"],
                "author_name": dict(m)["author_name"],
                "author_role": dict(m)["author_role"],
                "text": dict(m)["text"],
                "priority": bool(dict(m)["priority"]),
                "created_at": dict(m)["created_at"],
            }
            for m in room_msgs
        ],
        "dms": [
            {
                "id": dict(m)["id"],
                "from_name": dict(m)["from_name"],
                "from_id": dict(m)["from_id"],
                "text": dict(m)["text"],
                "priority": bool(dict(m)["priority"]),
                "created_at": dict(m)["created_at"],
            }
            for m in dm_msgs
        ],
        "documents": [
            {
                "id": dict(d)["id"],
                "room": dict(d)["room_name"],
                "filename": dict(d)["filename"],
                "size": dict(d)["size"],
                "uploaded_by": dict(d)["uploaded_by"],
                "created_at": dict(d)["created_at"],
            }
            for d in new_docs
        ],
    }


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@app.post("/api/v1/rooms/{room_name}/documents")
async def upload_document(room_name: str, file: UploadFile = File(...), agent: dict = Depends(auth_agent)):
    db = await get_db()
    room_id = await _require_room_member(db, room_name, agent["id"])
    doc_id = _uid()
    room_doc_dir = DOCS_DIR / room_id
    room_doc_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (50 MB max)")
    safe_name = Path(file.filename).name or "unnamed"
    (room_doc_dir / f"{doc_id}_{safe_name}").write_bytes(content)
    await db.execute(
        "INSERT INTO documents (id, room_id, filename, content_type, uploaded_by, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, room_id, safe_name, file.content_type, agent["id"], len(content), _now()),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members, exclude=agent["id"])
    return {"id": doc_id, "filename": safe_name, "size": len(content)}


@app.get("/api/v1/rooms/{room_name}/documents")
async def list_documents(room_name: str, agent: dict = Depends(auth_agent)):
    db = await get_db()
    room_id = await _require_room_member(db, room_name, agent["id"])
    docs = await db.execute_fetchall("SELECT * FROM documents WHERE room_id = ? ORDER BY created_at", (room_id,))
    return [
        {
            "id": dict(d)["id"],
            "filename": dict(d)["filename"],
            "size": dict(d)["size"],
            "uploaded_by": dict(d)["uploaded_by"],
            "created_at": dict(d)["created_at"],
        }
        for d in docs
    ]


@app.get("/api/v1/documents/{doc_id}/download")
async def download_document(doc_id: str, _auth: dict = Depends(auth_any)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not rows:
        raise HTTPException(404, "Document not found")
    doc = dict(rows[0])
    room_doc_dir = DOCS_DIR / doc["room_id"]
    for f in room_doc_dir.iterdir():
        if f.name.startswith(doc_id):
            return FileResponse(f, filename=doc["filename"], media_type=doc["content_type"])
    raise HTTPException(404, "File not found on disk")


# ===========================================================================
# Human Web UI
# ===========================================================================

LOGIN_HTML = (_TEMPLATE_DIR / "login.html").read_text()


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_HTML


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    if form.get("user") == HUMAN_USER and form.get("password") == HUMAN_PASS:
        db = await get_db()
        tok = secrets.token_hex(32)
        await db.execute("INSERT INTO sessions (token, created_at) VALUES (?, ?)", (tok, _now()))
        await db.commit()
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("chait_session", tok, httponly=True, samesite="lax", max_age=86400 * 7)
        return resp
    logger.warning("Login failed for user '%s' from %s", form.get("user", ""), request.client.host if request.client else "unknown")
    return HTMLResponse(
        "<html><body style='background:#0f172a;color:#f87171;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh'>Invalid credentials. <a href='/login' style='color:#38bdf8;margin-left:8px'>Retry</a></body></html>",
        401,
    )


@app.post("/logout")
async def logout(request: Request):
    session_token = request.cookies.get("chait_session")
    if session_token:
        db = await get_db()
        await db.execute("DELETE FROM sessions WHERE token = ?", (session_token,))
        await db.commit()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("chait_session")
    return resp


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------
DASHBOARD_HTML = (_TEMPLATE_DIR / "dashboard.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    session = await auth_human(request)
    if not session:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(DASHBOARD_HTML)


# ---------------------------------------------------------------------------
# UI API (human god-mode endpoints)
# ---------------------------------------------------------------------------
@app.post("/ui/api/rooms")
async def ui_create_room(request: Request, session: str = Depends(require_human)):
    """Human creates a room. Returns join token."""
    body = await request.json()
    name = body.get("name", "")
    topic = body.get("topic", "")
    if not name:
        raise HTTPException(400, "name required")
    db = await get_db()
    existing = await db.execute_fetchall("SELECT id, join_token FROM rooms WHERE name = ?", (name,))
    if existing:
        return {
            "id": dict(existing[0])["id"],
            "name": name,
            "join_token": dict(existing[0])["join_token"],
            "existing": True,
        }
    room_id = _uid()
    join_token = f"chait-{secrets.token_hex(16)}"
    await db.execute(
        "INSERT INTO rooms (id, name, topic, status, join_token, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
        (room_id, name, topic, join_token, _now()),
    )
    await db.commit()
    return {"id": room_id, "name": name, "topic": topic, "status": "active", "join_token": join_token}


@app.get("/ui/api/rooms")
async def ui_rooms(session: str = Depends(require_human)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id, name, topic, status, created_at FROM rooms ORDER BY created_at")
    return [dict(r) for r in rows]


@app.get("/ui/api/rooms/{room_name}/token")
async def ui_room_token(room_name: str, session: str = Depends(require_human)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT join_token FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404)
    return {"join_token": dict(rows[0])["join_token"]}


@app.get("/ui/api/rooms/{room_name}/messages")
async def ui_messages(room_name: str, since: Optional[str] = None, session: str = Depends(require_human)):
    db = await get_db()
    room_id = await _get_room_id(db, room_name)
    if since:
        msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE room_id = ? AND created_at > ? ORDER BY created_at LIMIT 200",
            (room_id, since),
        )
    else:
        msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE room_id = ? ORDER BY created_at DESC LIMIT 200", (room_id,)
        )
        msgs = list(reversed(msgs))
    return [_msg_dict(m) for m in msgs]


@app.get("/ui/api/rooms/{room_name}/details")
async def ui_room_details(room_name: str, session: str = Depends(require_human)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404)
    room = dict(rows[0])
    room.pop("join_token", None)
    members = await db.execute_fetchall(
        "SELECT a.id, a.name, a.role, a.card FROM agents a JOIN room_members rm ON a.id = rm.agent_id WHERE rm.room_id = ?",
        (room["id"],),
    )
    room["members"] = [
        {
            "id": dict(m)["id"],
            "name": dict(m)["name"],
            "role": dict(m)["role"],
            "card": _parse_card(dict(m).get("card")),
        }
        for m in members
    ]
    return room


@app.get("/ui/api/rooms/{room_name}/documents")
async def ui_room_docs(room_name: str, session: str = Depends(require_human)):
    db = await get_db()
    room_id = await _get_room_id(db, room_name)
    docs = await db.execute_fetchall("SELECT * FROM documents WHERE room_id = ? ORDER BY created_at", (room_id,))
    return [dict(d) for d in docs]


@app.post("/ui/api/rooms/{room_name}/messages")
async def ui_send_message(room_name: str, request: Request, session: str = Depends(require_human)):
    body = await request.json()
    text = body.get("text", "")
    if not text:
        raise HTTPException(400)
    if len(text) > MAX_MESSAGE_LENGTH:
        raise HTTPException(413, f"Message too long ({MAX_MESSAGE_LENGTH} chars max)")
    db = await get_db()
    room_id = await _get_room_id(db, room_name)
    msg_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO messages (id, room_id, author_id, author_name, author_role, text, reply_to, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, room_id, "human", "Human", "god", text, None, 1, now),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members)
    return {
        "id": msg_id, "room": room_name, "author_id": "human",
        "author_name": "Human", "author_role": "god",
        "text": text, "reply_to": None, "priority": True, "created_at": now,
    }


@app.post("/ui/api/rooms/{room_name}/documents")
async def ui_upload_document(room_name: str, file: UploadFile = File(...), session: str = Depends(require_human)):
    db = await get_db()
    room_id = await _get_room_id(db, room_name)
    doc_id = _uid()
    room_doc_dir = DOCS_DIR / room_id
    room_doc_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (50 MB max)")
    safe_name = Path(file.filename).name or "unnamed"
    (room_doc_dir / f"{doc_id}_{safe_name}").write_bytes(content)
    await db.execute(
        "INSERT INTO documents (id, room_id, filename, content_type, uploaded_by, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, room_id, safe_name, file.content_type, "human", len(content), _now()),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members)
    return {"id": doc_id, "filename": safe_name, "size": len(content)}


@app.post("/ui/api/dm/{target_id}")
async def ui_send_dm(target_id: str, request: Request, session: str = Depends(require_human)):
    body = await request.json()
    text = body.get("text", "")
    if not text:
        raise HTTPException(400)
    if len(text) > MAX_MESSAGE_LENGTH:
        raise HTTPException(413, f"Message too long ({MAX_MESSAGE_LENGTH} chars max)")
    db = await get_db()
    dm_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO dms (id, from_id, from_name, to_id, text, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (dm_id, "human", "Human", target_id, text, 1, now),
    )
    await db.commit()
    _notify_agent(target_id)
    return {
        "id": dm_id, "from_id": "human", "from_name": "Human",
        "to_id": target_id, "text": text, "priority": True, "created_at": now,
    }


@app.get("/ui/api/dm/{agent_id}")
async def ui_get_dms(agent_id: str, session: str = Depends(require_human)):
    """Read DM history between human and an agent."""
    db = await get_db()
    msgs = await db.execute_fetchall(
        "SELECT * FROM dms WHERE (from_id = 'human' AND to_id = ?) OR (from_id = ? AND to_id = 'human') ORDER BY created_at LIMIT 200",
        (agent_id, agent_id),
    )
    return [_dm_dict(m) for m in msgs]


@app.get("/ui/api/rooms/activity")
async def ui_rooms_activity(session: str = Depends(require_human)):
    """Return last message timestamp per room for unread indicators."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT r.name, MAX(m.created_at) as last_activity "
        "FROM rooms r LEFT JOIN messages m ON r.id = m.room_id "
        "GROUP BY r.id"
    )
    return {dict(r)["name"]: dict(r)["last_activity"] for r in rows}


@app.post("/ui/api/rooms/{room_name}/status")
async def ui_set_room_status(room_name: str, request: Request, session: str = Depends(require_human)):
    body = await request.json()
    new_status = body.get("status")
    if new_status not in VALID_ROOM_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(VALID_ROOM_STATUSES)}")
    db = await get_db()
    room_id = await _get_room_id(db, room_name)
    await db.execute("UPDATE rooms SET status = ? WHERE id = ?", (new_status, room_id))
    await db.commit()
    return {"room": room_name, "status": new_status}


# ---------------------------------------------------------------------------
# API tokens management (human-only)
# ---------------------------------------------------------------------------
@app.post("/ui/api/tokens")
async def ui_create_api_token(session: str = Depends(require_human)):
    """Generate a new API token for CLI use (e.g. launch.sh)."""
    db = await get_db()
    token_id = _uid()
    token = f"chait-api-{secrets.token_hex(24)}"
    await db.execute(
        "INSERT INTO api_tokens (id, token, created_at) VALUES (?, ?, ?)",
        (token_id, token, _now()),
    )
    await db.commit()
    return {"id": token_id, "token": token}


@app.get("/ui/api/tokens")
async def ui_list_api_tokens(session: str = Depends(require_human)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id, token, created_at FROM api_tokens ORDER BY created_at")
    return [{"id": dict(r)["id"], "token": dict(r)["token"], "created_at": dict(r)["created_at"]} for r in rows]


@app.delete("/ui/api/tokens/{token_id}")
async def ui_revoke_api_token(token_id: str, session: str = Depends(require_human)):
    db = await get_db()
    await db.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
    await db.commit()
    return {"revoked": True, "id": token_id}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if HUMAN_PASS == "changeme":
        HUMAN_PASS = secrets.token_urlsafe(16)
        logger.warning("No CHAIT_HUMAN_PASS set. Generated: %s", HUMAN_PASS)
    host = os.getenv("CHAIT_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=PORT, log_level="info")
