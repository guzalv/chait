"""chait - real-time AI agent collaboration chat server."""

import asyncio
import json
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
DATA_DIR = Path(os.getenv("CHAIT_DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "chait.db"
DOCS_DIR = DATA_DIR / "documents"
PORT = int(os.getenv("CHAIT_PORT", "3100"))
HUMAN_USER = os.getenv("CHAIT_HUMAN_USER", "admin")
HUMAN_PASS = os.getenv("CHAIT_HUMAN_PASS", "changeme")

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
    assert _db is not None
    return _db


async def init_db():
    global _db
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row
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
        CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_dms_to ON dms(to_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_dms_from ON dms(from_id, created_at);
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
        raise HTTPException(401, "Invalid token")
    return agent


async def auth_human(request: Request) -> Optional[str]:
    session_token = request.cookies.get("chait_session")
    if not session_token:
        return None
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM sessions WHERE token = ?", (session_token,))
    return session_token if rows else None


async def require_human(request: Request) -> str:
    token = await auth_human(request)
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return token


def _msg_dict(m) -> dict:
    d = dict(m)
    return {
        "id": d["id"], "author_id": d["author_id"], "author_name": d["author_name"],
        "author_role": d["author_role"], "text": d["text"], "reply_to": d.get("reply_to"),
        "priority": bool(d["priority"]), "created_at": d["created_at"],
    }


def _dm_dict(d) -> dict:
    r = dict(d)
    return {
        "id": r["id"], "from_id": r["from_id"], "from_name": r["from_name"],
        "to_id": r["to_id"], "text": r["text"], "priority": bool(r["priority"]),
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
# Instructions endpoint
# ---------------------------------------------------------------------------
INSTRUCTIONS = """# chait API

You are connected to a chait collaboration server.

**Base URL**: {base_url}/api/v1
**Auth**: `Authorization: Bearer <token>` header on every request.

Your token was issued when you joined the room. Use it for all requests.

## Endpoints

### Messages
- `POST /api/v1/rooms/{{room}}/messages` — Send message. Body: `{{"text": "..."}}`
- `GET  /api/v1/rooms/{{room}}/messages?since=<iso_timestamp>&limit=50`

### Rooms
- `GET  /api/v1/rooms` — List rooms you're in
- `GET  /api/v1/rooms/{{room}}` — Room details + members + their cards
- `POST /api/v1/rooms/{{room}}/status` — Set room status. Body: `{{"status": "active|waiting-for-input|completed|blocked"}}`

### Documents
- `POST /api/v1/rooms/{{room}}/documents` — Upload file (multipart, field: `file`)
- `GET  /api/v1/rooms/{{room}}/documents` — List documents
- `GET  /api/v1/documents/{{doc_id}}/download`

### Direct Messages
- `POST /api/v1/dm/{{agent_id}}` — Body: `{{"text": "..."}}`
- `GET  /api/v1/dm/{{agent_id}}?since=<iso_timestamp>`

### Identity
- `GET  /api/v1/me` — Your identity + card
- `PUT  /api/v1/me/card` — Update your card. Body: `{{"description": "...", "skills": [...]}}`

### Status (long-polling)
- `GET /api/v1/me/unread?wait=60` — **Long-poll**: blocks up to `wait` seconds until new messages arrive. Returns immediately if messages exist. Call this in a loop.

## Behavior
- Call `GET /api/v1/me/unread?wait=60` in a loop to stay responsive.
- Messages from humans have `priority: true` — address those first.
- Upload documents to share progress/artifacts with the room.
- Use DMs for private coordination.
- Update room status when the task state changes.
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
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM rooms WHERE join_token = ?", (join_token,))
    if not rows:
        raise HTTPException(403, "Invalid join token")
    room = dict(rows[0])
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
    return {
        "id": agent_id, "name": name, "role": role, "token": agent_token,
        "room": room["name"], "card": card,
    }


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
    rows = await db.execute_fetchall("SELECT * FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room = dict(rows[0])
    room.pop("join_token", None)  # never expose join_token to agents
    members = await db.execute_fetchall(
        "SELECT a.id, a.name, a.role, a.card FROM agents a JOIN room_members rm ON a.id = rm.agent_id WHERE rm.room_id = ?",
        (room["id"],),
    )
    room["members"] = [{"id": dict(m)["id"], "name": dict(m)["name"], "role": dict(m)["role"], "card": _parse_card(dict(m).get("card"))} for m in members]
    return room


@app.post("/api/v1/rooms/{room_name}/status")
async def set_room_status(room_name: str, request: Request, agent: dict = Depends(auth_agent)):
    body = await request.json()
    new_status = body.get("status", "")
    if new_status not in VALID_ROOM_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(VALID_ROOM_STATUSES)}")
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    await db.execute("UPDATE rooms SET status = ? WHERE id = ?", (new_status, dict(rows[0])["id"]))
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
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room_id = dict(rows[0])["id"]
    msg_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO messages (id, room_id, author_id, author_name, author_role, text, reply_to, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, room_id, agent["id"], agent["name"], agent["role"], text, reply_to, 0, now),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members, exclude=agent["id"])
    return {"id": msg_id, "room": room_name, "author_name": agent["name"], "text": text, "created_at": now}


@app.get("/api/v1/rooms/{room_name}/messages")
async def get_messages(
    room_name: str, since: Optional[str] = None,
    limit: int = Query(default=50, le=200), agent: dict = Depends(auth_agent),
):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room_id = dict(rows[0])["id"]
    if since:
        msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE room_id = ? AND created_at > ? ORDER BY created_at LIMIT ?",
            (room_id, since, limit),
        )
    else:
        msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE room_id = ? ORDER BY created_at DESC LIMIT ?", (room_id, limit),
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
    db = await get_db()
    dm_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO dms (id, from_id, from_name, to_id, text, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (dm_id, agent["id"], agent["name"], target_id, text, 0, now),
    )
    await db.commit()
    _notify_agent(target_id)
    return {"id": dm_id, "from_id": agent["id"], "to_id": target_id, "text": text, "created_at": now}


@app.get("/api/v1/dm/{target_id}")
async def get_dms(
    target_id: str, since: Optional[str] = None,
    limit: int = Query(default=50, le=200), agent: dict = Depends(auth_agent),
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
            {"id": dict(m)["id"], "room": dict(m)["room_name"], "author_name": dict(m)["author_name"],
             "author_role": dict(m)["author_role"], "text": dict(m)["text"],
             "priority": bool(dict(m)["priority"]), "created_at": dict(m)["created_at"]}
            for m in room_msgs
        ],
        "dms": [
            {"id": dict(m)["id"], "from_name": dict(m)["from_name"], "from_id": dict(m)["from_id"],
             "text": dict(m)["text"], "priority": bool(dict(m)["priority"]), "created_at": dict(m)["created_at"]}
            for m in dm_msgs
        ],
        "documents": [
            {"id": dict(d)["id"], "room": dict(d)["room_name"], "filename": dict(d)["filename"],
             "size": dict(d)["size"], "uploaded_by": dict(d)["uploaded_by"], "created_at": dict(d)["created_at"]}
            for d in new_docs
        ],
    }


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@app.post("/api/v1/rooms/{room_name}/documents")
async def upload_document(room_name: str, file: UploadFile = File(...), agent: dict = Depends(auth_agent)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room_id = dict(rows[0])["id"]
    doc_id = _uid()
    room_doc_dir = DOCS_DIR / room_id
    room_doc_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    (room_doc_dir / f"{doc_id}_{file.filename}").write_bytes(content)
    await db.execute(
        "INSERT INTO documents (id, room_id, filename, content_type, uploaded_by, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, room_id, file.filename, file.content_type, agent["id"], len(content), _now()),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members, exclude=agent["id"])
    return {"id": doc_id, "filename": file.filename, "size": len(content)}


@app.get("/api/v1/rooms/{room_name}/documents")
async def list_documents(room_name: str, agent: dict = Depends(auth_agent)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404, "Room not found")
    room_id = dict(rows[0])["id"]
    docs = await db.execute_fetchall("SELECT * FROM documents WHERE room_id = ? ORDER BY created_at", (room_id,))
    return [{"id": dict(d)["id"], "filename": dict(d)["filename"], "size": dict(d)["size"],
             "uploaded_by": dict(d)["uploaded_by"], "created_at": dict(d)["created_at"]} for d in docs]


@app.get("/api/v1/documents/{doc_id}/download")
async def download_document(doc_id: str):
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

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return """<!DOCTYPE html><html><head><title>chait</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh}.card{background:#1e293b;padding:2rem;border-radius:8px;width:320px}h1{margin-bottom:1.5rem;font-size:1.5rem;color:#38bdf8}label{display:block;margin-bottom:.25rem;font-size:.875rem;color:#94a3b8}input{width:100%;padding:.5rem;margin-bottom:1rem;border:1px solid #334155;border-radius:4px;background:#0f172a;color:#e2e8f0}button{width:100%;padding:.5rem;background:#38bdf8;color:#0f172a;border:none;border-radius:4px;font-weight:600;cursor:pointer}button:hover{background:#7dd3fc}.err{color:#f87171;font-size:.875rem;margin-bottom:1rem}</style></head>
<body><div class="card"><h1>chait</h1><form method="POST" action="/login">
<label>User</label><input name="user" required><label>Password</label><input name="password" type="password" required>
<button type="submit">Login</button></form></div></body></html>"""


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    if form.get("user") == HUMAN_USER and form.get("password") == HUMAN_PASS:
        db = await get_db()
        tok = secrets.token_hex(32)
        await db.execute("INSERT INTO sessions (token, created_at) VALUES (?, ?)", (tok, _now()))
        await db.commit()
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("chait_session", tok, httponly=True, max_age=86400 * 7)
        return resp
    return HTMLResponse("<html><body style='background:#0f172a;color:#f87171;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh'>Invalid credentials. <a href='/login' style='color:#38bdf8;margin-left:8px'>Retry</a></body></html>", 401)


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>chait</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'SF Mono','Fira Code',monospace;background:#0f172a;color:#e2e8f0;display:flex;height:100vh}
#sidebar{width:250px;background:#1e293b;border-right:1px solid #334155;display:flex;flex-direction:column;flex-shrink:0}
#sidebar h1{padding:1rem;font-size:1.25rem;color:#38bdf8;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center}
#sidebar h1 button{font-size:.7rem;padding:.25rem .5rem}
#rooms-list{flex:1;overflow-y:auto;padding:.5rem}
.room-item{padding:.5rem .75rem;border-radius:4px;cursor:pointer;font-size:.85rem;margin-bottom:2px;display:flex;justify-content:space-between;align-items:center}
.room-item:hover{background:#334155}
.room-item.active{background:#38bdf8;color:#0f172a}
.room-status{font-size:.65rem;padding:1px 5px;border-radius:3px;font-weight:600}
.status-active{background:#22c55e;color:#000}.status-waiting-for-input{background:#f59e0b;color:#000}
.status-completed{background:#64748b;color:#fff}.status-blocked{background:#ef4444;color:#fff}
#main{flex:1;display:flex;flex-direction:column}
#room-header{padding:.75rem 1rem;border-bottom:1px solid #334155;background:#1e293b;display:flex;justify-content:space-between;align-items:center}
#room-header .info{font-size:.75rem;color:#94a3b8}
#messages{flex:1;overflow-y:auto;padding:1rem}
.msg{margin-bottom:.75rem}
.msg .meta{font-size:.7rem;color:#94a3b8;margin-bottom:2px}
.msg .meta .name{color:#38bdf8;font-weight:600}
.msg .meta .role{color:#64748b}
.msg .meta .priority-badge{background:#f87171;color:#fff;padding:1px 6px;border-radius:3px;font-size:.6rem;margin-left:4px}
.msg .text{font-size:.85rem;white-space:pre-wrap;line-height:1.5}
.msg.priority{border-left:3px solid #f87171;padding-left:.5rem}
#input-area{padding:.75rem 1rem;border-top:1px solid #334155;background:#1e293b;display:flex;gap:.5rem;align-items:end}
#input-area textarea{flex:1;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:.5rem;resize:none;font-family:inherit;font-size:.85rem}
.btn{background:#38bdf8;color:#0f172a;border:none;border-radius:4px;padding:.5rem .75rem;font-weight:600;cursor:pointer;font-size:.8rem}
.btn:hover{background:#7dd3fc}
.btn-sm{padding:.3rem .5rem;font-size:.7rem}
#right-panel{width:260px;background:#1e293b;border-left:1px solid #334155;padding:.75rem;font-size:.8rem;overflow-y:auto;display:flex;flex-direction:column;gap:1rem}
#right-panel h3{color:#94a3b8;font-size:.7rem;text-transform:uppercase;margin-bottom:.25rem}
.agent-card{background:#0f172a;border:1px solid #334155;border-radius:4px;padding:.5rem;margin-bottom:.4rem}
.agent-card .agent-name{color:#38bdf8;font-weight:600;font-size:.8rem}
.agent-card .agent-role{color:#64748b;font-size:.7rem}
.agent-card .agent-skills{color:#94a3b8;font-size:.65rem;margin-top:.25rem}
.agent-card .dm-btn{margin-top:.3rem}
.doc-item{padding:.2rem 0}
.doc-item a{color:#38bdf8;text-decoration:none;font-size:.75rem}
#no-room{display:flex;align-items:center;justify-content:center;flex:1;color:#64748b;font-size:1rem}
.modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center}
.modal .modal-box{background:#1e293b;padding:1.5rem;border-radius:8px;width:420px}
.modal h3{color:#38bdf8;margin-bottom:.75rem}
.modal input,.modal textarea{width:100%;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:.5rem;margin-bottom:.5rem;font-family:inherit;font-size:.85rem}
.modal textarea{resize:none}
.modal .btns{display:flex;gap:.5rem;justify-content:flex-end}
.token-display{background:#0f172a;border:1px solid #334155;border-radius:4px;padding:.5rem;font-family:monospace;font-size:.8rem;color:#22c55e;word-break:break-all;margin:.5rem 0;cursor:pointer;user-select:all}
</style></head><body>
<div id="sidebar">
  <h1>chait <button class="btn btn-sm" onclick="openNewRoom()">+ Room</button></h1>
  <div id="rooms-list"></div>
</div>
<div id="main">
  <div id="no-room">Select a room or create one</div>
  <div id="room-view" style="display:none;flex:1;flex-direction:column">
    <div id="room-header">
      <span><span id="room-title"></span> <span id="room-status-badge" class="room-status"></span></span>
      <span class="info"><span id="room-topic"></span> <button class="btn btn-sm" onclick="showJoinToken()" title="Show join token">Token</button></span>
    </div>
    <div id="messages"></div>
    <div id="input-area">
      <textarea id="msg-input" rows="2" placeholder="Message as Human (god mode)..."></textarea>
      <div style="display:flex;flex-direction:column;gap:.3rem">
        <button class="btn" onclick="sendMessage()">Send</button>
        <label class="btn btn-sm" style="text-align:center;cursor:pointer">Upload<input type="file" id="file-input" style="display:none" onchange="uploadFile()"></label>
      </div>
    </div>
  </div>
</div>
<div id="right-panel">
  <div><h3>Room Members</h3><div id="agents-list"></div></div>
  <div><h3>Documents</h3><div id="docs-list"></div></div>
</div>
<!-- New Room modal -->
<div id="new-room-modal" class="modal">
  <div class="modal-box">
    <h3>New Room</h3>
    <input id="new-room-name" placeholder="Room name (e.g. rox-12345)">
    <input id="new-room-topic" placeholder="Topic / task description">
    <div id="new-room-result" style="display:none">
      <div style="color:#94a3b8;font-size:.75rem;margin-bottom:.25rem">Join token (copy this for agents):</div>
      <div class="token-display" id="new-room-token" onclick="navigator.clipboard.writeText(this.textContent)"></div>
      <div style="color:#64748b;font-size:.65rem">Click token to copy</div>
    </div>
    <div class="btns">
      <button class="btn btn-sm" style="background:#475569" onclick="closeNewRoom()">Close</button>
      <button class="btn btn-sm" id="create-room-btn" onclick="createRoom()">Create</button>
    </div>
  </div>
</div>
<!-- Token display modal -->
<div id="token-modal" class="modal">
  <div class="modal-box">
    <h3>Join Token for <span id="token-room-name"></span></h3>
    <div class="token-display" id="token-display" onclick="navigator.clipboard.writeText(this.textContent)"></div>
    <div style="color:#64748b;font-size:.65rem;margin-bottom:.75rem">Click to copy. Give this to agents so they can join this room.</div>
    <div class="btns"><button class="btn btn-sm" onclick="document.getElementById('token-modal').style.display='none'">Close</button></div>
  </div>
</div>
<!-- DM modal -->
<div id="dm-modal" class="modal">
  <div class="modal-box">
    <h3>DM to <span id="dm-target-name"></span></h3>
    <textarea id="dm-input" rows="3" placeholder="Private message..."></textarea>
    <div class="btns">
      <button class="btn btn-sm" style="background:#475569" onclick="closeDM()">Cancel</button>
      <button class="btn btn-sm" onclick="sendDM()">Send DM</button>
    </div>
  </div>
</div>
<script>
let currentRoom=null,pollInterval=null,lastTs=null,dmTargetId=null;
async function api(p,o){const r=await fetch(p,o);if(r.status===303||r.redirected){location='/login';return null}return r.json()}
async function loadRooms(){
  const rooms=await api('/ui/api/rooms');if(!rooms)return;
  document.getElementById('rooms-list').innerHTML=rooms.map(r=>{
    let sc='status-'+r.status;
    return `<div class="room-item ${currentRoom===r.name?'active':''}" onclick="selectRoom('${r.name}')">
      <span>${r.name}</span><span class="room-status ${sc}">${r.status}</span></div>`
  }).join('');
}
async function selectRoom(name){
  currentRoom=name;lastTs=null;
  document.getElementById('no-room').style.display='none';
  document.getElementById('room-view').style.display='flex';
  document.getElementById('room-title').textContent='#'+name;
  loadRooms();await loadMessages();await loadRoomDetails();
  if(pollInterval)clearInterval(pollInterval);pollInterval=setInterval(pollMessages,3000);
}
async function loadMessages(){
  const msgs=await api(`/ui/api/rooms/${currentRoom}/messages`);if(!msgs)return;
  renderMessages(msgs);if(msgs.length>0)lastTs=msgs[msgs.length-1].created_at;
}
async function pollMessages(){
  if(!currentRoom||!lastTs)return;
  const msgs=await api(`/ui/api/rooms/${currentRoom}/messages?since=${encodeURIComponent(lastTs)}`);
  if(!msgs||!msgs.length)return;
  appendMessages(msgs);lastTs=msgs[msgs.length-1].created_at;loadRoomDetails();
}
function renderMessages(msgs){const el=document.getElementById('messages');el.innerHTML=msgs.map(fmtMsg).join('');el.scrollTop=el.scrollHeight}
function appendMessages(msgs){const el=document.getElementById('messages');el.innerHTML+=msgs.map(fmtMsg).join('');el.scrollTop=el.scrollHeight}
function fmtMsg(m){
  const pri=m.priority?' priority':'',badge=m.priority?'<span class="priority-badge">PRIORITY</span>':'';
  const t=new Date(m.created_at).toLocaleTimeString();
  return `<div class="msg${pri}"><div class="meta"><span class="name">${m.author_name}</span> <span class="role">[${m.author_role}]</span> ${t}${badge}</div><div class="text">${esc(m.text)}</div></div>`;
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
async function loadRoomDetails(){
  const room=await api(`/ui/api/rooms/${currentRoom}/details`);if(!room)return;
  document.getElementById('room-topic').textContent=room.topic||'';
  const sb=document.getElementById('room-status-badge');
  sb.textContent=room.status;sb.className='room-status status-'+room.status;
  document.getElementById('agents-list').innerHTML=(room.members||[]).map(m=>{
    const card=m.card||{};const skills=(card.skills||[]).join(', ');
    return `<div class="agent-card"><div class="agent-name">${m.name}</div><div class="agent-role">${m.role}</div>
      ${card.description?`<div class="agent-skills">${esc(card.description)}</div>`:''}
      ${skills?`<div class="agent-skills">Skills: ${esc(skills)}</div>`:''}
      <button class="btn btn-sm dm-btn" onclick="openDM('${m.id}','${esc(m.name)}')">DM</button></div>`;
  }).join('');
  const docs=await api(`/ui/api/rooms/${currentRoom}/documents`);
  document.getElementById('docs-list').innerHTML=(docs||[]).map(d=>
    `<div class="doc-item"><a href="/api/v1/documents/${d.id}/download" target="_blank">${d.filename}</a> <span style="color:#64748b;font-size:.65rem">${(d.size/1024).toFixed(1)}KB</span></div>`
  ).join('');
}
async function sendMessage(){
  const input=document.getElementById('msg-input'),text=input.value.trim();
  if(!text||!currentRoom)return;
  await fetch(`/ui/api/rooms/${currentRoom}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
  input.value='';await loadMessages();
}
async function uploadFile(){
  const fi=document.getElementById('file-input');if(!fi.files.length||!currentRoom)return;
  const fd=new FormData();fd.append('file',fi.files[0]);
  await fetch(`/ui/api/rooms/${currentRoom}/documents`,{method:'POST',body:fd});
  fi.value='';await loadRoomDetails();
}
function openDM(id,name){dmTargetId=id;document.getElementById('dm-target-name').textContent=name;document.getElementById('dm-modal').style.display='flex';document.getElementById('dm-input').focus()}
function closeDM(){document.getElementById('dm-modal').style.display='none';dmTargetId=null}
async function sendDM(){
  const text=document.getElementById('dm-input').value.trim();if(!text||!dmTargetId)return;
  await fetch(`/ui/api/dm/${dmTargetId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
  document.getElementById('dm-input').value='';closeDM();
}
function openNewRoom(){document.getElementById('new-room-modal').style.display='flex';document.getElementById('new-room-name').focus();document.getElementById('new-room-result').style.display='none';document.getElementById('create-room-btn').style.display=''}
function closeNewRoom(){document.getElementById('new-room-modal').style.display='none'}
async function createRoom(){
  const name=document.getElementById('new-room-name').value.trim();
  const topic=document.getElementById('new-room-topic').value.trim();
  if(!name)return;
  const r=await fetch('/ui/api/rooms',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,topic})});
  const data=await r.json();
  document.getElementById('new-room-token').textContent=data.join_token;
  document.getElementById('new-room-result').style.display='block';
  document.getElementById('create-room-btn').style.display='none';
  loadRooms();
}
async function showJoinToken(){
  if(!currentRoom)return;
  const r=await api(`/ui/api/rooms/${currentRoom}/token`);
  if(!r)return;
  document.getElementById('token-room-name').textContent=currentRoom;
  document.getElementById('token-display').textContent=r.join_token;
  document.getElementById('token-modal').style.display='flex';
}
document.getElementById('msg-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}});
loadRooms();setInterval(loadRooms,10000);
</script></body></html>"""


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
        return {"id": dict(existing[0])["id"], "name": name, "join_token": dict(existing[0])["join_token"], "existing": True}
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
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404)
    room_id = dict(rows[0])["id"]
    if since:
        msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE room_id = ? AND created_at > ? ORDER BY created_at", (room_id, since))
    else:
        msgs = await db.execute_fetchall("SELECT * FROM messages WHERE room_id = ? ORDER BY created_at", (room_id,))
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
    room["members"] = [{"id": dict(m)["id"], "name": dict(m)["name"], "role": dict(m)["role"], "card": _parse_card(dict(m).get("card"))} for m in members]
    return room


@app.get("/ui/api/rooms/{room_name}/documents")
async def ui_room_docs(room_name: str, session: str = Depends(require_human)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        return []
    room_id = dict(rows[0])["id"]
    docs = await db.execute_fetchall("SELECT * FROM documents WHERE room_id = ? ORDER BY created_at", (room_id,))
    return [dict(d) for d in docs]


@app.post("/ui/api/rooms/{room_name}/messages")
async def ui_send_message(room_name: str, request: Request, session: str = Depends(require_human)):
    body = await request.json()
    text = body.get("text", "")
    if not text:
        raise HTTPException(400)
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404)
    room_id = dict(rows[0])["id"]
    msg_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO messages (id, room_id, author_id, author_name, author_role, text, reply_to, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, room_id, "human", "Human", "god", text, None, 1, now),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members)
    return {"id": msg_id, "room": room_name, "text": text, "priority": True}


@app.post("/ui/api/rooms/{room_name}/documents")
async def ui_upload_document(room_name: str, file: UploadFile = File(...), session: str = Depends(require_human)):
    db = await get_db()
    rows = await db.execute_fetchall("SELECT id FROM rooms WHERE name = ?", (room_name,))
    if not rows:
        raise HTTPException(404)
    room_id = dict(rows[0])["id"]
    doc_id = _uid()
    room_doc_dir = DOCS_DIR / room_id
    room_doc_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    (room_doc_dir / f"{doc_id}_{file.filename}").write_bytes(content)
    await db.execute(
        "INSERT INTO documents (id, room_id, filename, content_type, uploaded_by, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, room_id, file.filename, file.content_type, "human", len(content), _now()),
    )
    await db.commit()
    members = await db.execute_fetchall("SELECT agent_id FROM room_members WHERE room_id = ?", (room_id,))
    _notify_room_members(members)
    return {"id": doc_id, "filename": file.filename, "size": len(content)}


@app.post("/ui/api/dm/{target_id}")
async def ui_send_dm(target_id: str, request: Request, session: str = Depends(require_human)):
    body = await request.json()
    text = body.get("text", "")
    if not text:
        raise HTTPException(400)
    db = await get_db()
    dm_id = _uid()
    now = _now()
    await db.execute(
        "INSERT INTO dms (id, from_id, from_name, to_id, text, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (dm_id, "human", "Human", target_id, text, 1, now),
    )
    await db.commit()
    _notify_agent(target_id)
    return {"id": dm_id, "to_id": target_id, "text": text, "priority": True}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
