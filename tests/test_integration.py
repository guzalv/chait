"""Integration tests for chait server - multi-agent scenarios.

Each test simulates scripted agent interactions against the server via TestClient
(in-memory, no running server). Threading is used for concurrent agents and
long-poll testing.
"""

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path):
    """Fresh TestClient with isolated temp DB per test."""
    server.DATA_DIR = tmp_path
    server.DB_PATH = tmp_path / "test.db"
    server.DOCS_DIR = tmp_path / "documents"
    server._db = None
    server._unread_events.clear()
    server._rate_buckets.clear()
    with TestClient(app=server.app) as c:
        yield c


def _login(client):
    r = client.post(
        "/login",
        data={"user": "admin", "password": "changeme"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _create_room(client, name, topic=""):
    r = client.post("/ui/api/rooms", json={"name": name, "topic": topic})
    assert r.status_code == 200
    return r.json()


def _join(client, join_token, name="Agent", role="agent", card=None):
    body = {"join_token": join_token, "name": name, "role": role}
    if card:
        body["card"] = card
    r = client.post("/api/v1/join", json=body)
    assert r.status_code == 200
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _post(client, room, text, token):
    r = client.post(
        f"/api/v1/rooms/{room}/messages",
        json={"text": text},
        headers=_auth(token),
    )
    assert r.status_code == 200
    return r.json()


def _unread(client, token, **params):
    r = client.get("/api/v1/me/unread", params=params, headers=_auth(token))
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------
# 1. Multi-agent room conversation
# ---------------------------------------------------------------------------
def test_multi_agent_room_conversation(client):
    _login(client)
    room = _create_room(client, "project-x")
    a = _join(client, room["join_token"], "Alice", "designer", {"skills": ["ui"]})
    b = _join(client, room["join_token"], "Bob", "developer", {"skills": ["go"]})
    c = _join(client, room["join_token"], "Carol", "tester", {"skills": ["pytest"]})

    # A posts
    _post(client, "project-x", "Here are the mockups", a["agent_token"])

    # B and C poll unread — both see A's message
    for ag in [b, c]:
        data = _unread(client, ag["agent_token"])
        assert any(m["text"] == "Here are the mockups" and m["room"] == "project-x" for m in data["room_messages"])

    # B replies
    _post(client, "project-x", "Looks good, implementing", b["agent_token"])

    # A and C see B's reply
    for ag in [a, c]:
        data = _unread(client, ag["agent_token"])
        assert any(m["text"] == "Looks good, implementing" for m in data["room_messages"])

    # All messages in correct order with correct authors
    msgs = client.get("/api/v1/rooms/project-x/messages", headers=_auth(a["agent_token"])).json()["data"]
    assert len(msgs) == 2
    assert msgs[0]["author_name"] == "Alice"
    assert msgs[1]["author_name"] == "Bob"
    assert msgs[0]["created_at"] <= msgs[1]["created_at"]


# ---------------------------------------------------------------------------
# 2. Long-poll notification delivery
# ---------------------------------------------------------------------------
def test_long_poll_notification_delivery(client):
    _login(client)
    room = _create_room(client, "poll-room")
    a = _join(client, room["join_token"], "Alice")
    b = _join(client, room["join_token"], "Bob")

    result = {}

    def poll():
        try:
            t0 = time.monotonic()
            r = client.get("/api/v1/me/unread", params={"wait": 10}, headers=_auth(b["agent_token"]))
            result["elapsed"] = time.monotonic() - t0
            result["data"] = r.json()
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=poll)
    t.start()
    time.sleep(1)  # let poll enter wait state

    _post(client, "poll-room", "wake up!", a["agent_token"])
    t.join(timeout=8)

    assert not t.is_alive(), "Long-poll thread still running"
    assert "error" not in result, f"Poll error: {result.get('error')}"
    assert result["elapsed"] < 5, f"Took {result['elapsed']:.1f}s, expected < 5"
    assert any(m["text"] == "wake up!" for m in result["data"]["room_messages"])


# ---------------------------------------------------------------------------
# 3. Human god-mode priority
# ---------------------------------------------------------------------------
def test_human_god_mode_priority(client):
    _login(client)
    room = _create_room(client, "priority-room")
    a = _join(client, room["join_token"], "Alice", "dev")
    b = _join(client, room["join_token"], "Bob", "dev")

    # Agent A posts normal message
    _post(client, "priority-room", "agent message", a["agent_token"])

    # Human posts priority message (session cookie already set by _login)
    r = client.post("/ui/api/rooms/priority-room/messages", json={"text": "urgent from human"})
    assert r.status_code == 200
    assert r.json()["priority"] is True

    # B polls unread — sees both
    data = _unread(client, b["agent_token"])
    room_msgs = data["room_messages"]

    agent_msg = next(m for m in room_msgs if m["text"] == "agent message")
    human_msg = next(m for m in room_msgs if m["text"] == "urgent from human")

    assert agent_msg["priority"] is False
    assert human_msg["priority"] is True
    assert human_msg["author_name"] == "Human"
    assert human_msg["author_role"] == "god"


# ---------------------------------------------------------------------------
# 4. DM routing
# ---------------------------------------------------------------------------
def test_dm_routing(client):
    _login(client)
    room = _create_room(client, "dm-room")
    a = _join(client, room["join_token"], "Alice")
    b = _join(client, room["join_token"], "Bob")
    c = _join(client, room["join_token"], "Carol")

    # A sends DM to B
    r = client.post(f"/api/v1/dm/{b['id']}", json={"text": "private to Bob"}, headers=_auth(a["agent_token"]))
    assert r.status_code == 200

    # B sees the DM
    b_data = _unread(client, b["agent_token"])
    assert any(d["text"] == "private to Bob" and d["from_id"] == a["id"] for d in b_data["dms"])

    # C does NOT see the DM
    c_data = _unread(client, c["agent_token"])
    assert len(c_data["dms"]) == 0

    # B reads DM history with A
    history = client.get(f"/api/v1/dm/{a['id']}", headers=_auth(b["agent_token"])).json()["data"]
    assert len(history) == 1
    assert history[0]["text"] == "private to Bob"
    assert history[0]["from_id"] == a["id"]
    assert history[0]["to_id"] == b["id"]


# ---------------------------------------------------------------------------
# 5. Room lifecycle (status transitions)
# ---------------------------------------------------------------------------
def test_room_lifecycle_status_transitions(client):
    _login(client)
    room = _create_room(client, "lifecycle-room")
    assert room["status"] == "active"
    agent = _join(client, room["join_token"])
    h = _auth(agent["agent_token"])

    transitions = ["waiting-for-input", "active", "completed"]
    for status in transitions:
        r = client.post("/api/v1/rooms/lifecycle-room/status", json={"status": status}, headers=h)
        assert r.status_code == 200
        assert r.json()["status"] == status

        # Verify via GET
        room_data = client.get("/api/v1/rooms/lifecycle-room", headers=h).json()
        assert room_data["status"] == status

    # Invalid status → 400
    r = client.post("/api/v1/rooms/lifecycle-room/status", json={"status": "nonsense"}, headers=h)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 6. Agent card visibility
# ---------------------------------------------------------------------------
def test_agent_card_visibility(client):
    card = {
        "description": "Full-stack engineer",
        "skills": ["python", "react"],
        "tools": ["pytest", "webpack"],
        "constraints": ["no-root-access"],
    }
    _login(client)
    room = _create_room(client, "card-room")
    agent = _join(client, room["join_token"], "CardAgent", "engineer", card)
    h = _auth(agent["agent_token"])

    # Card present in room member info
    room_data = client.get("/api/v1/rooms/card-room", headers=h).json()
    member = room_data["members"][0]
    assert member["card"] == card

    # Update card
    new_card = {"description": "Senior engineer", "skills": ["python", "react", "go"]}
    r = client.put("/api/v1/me/card", json=new_card, headers=h)
    assert r.status_code == 200

    # Updated card in room
    room_data = client.get("/api/v1/rooms/card-room", headers=h).json()
    assert room_data["members"][0]["card"] == new_card

    # Verify via /me endpoint
    me = client.get("/api/v1/me", headers=h).json()
    assert me["card"] == new_card


# ---------------------------------------------------------------------------
# 7. Document sharing
# ---------------------------------------------------------------------------
def test_document_sharing(client):
    _login(client)
    room = _create_room(client, "docs-room")
    a = _join(client, room["join_token"], "Alice")
    b = _join(client, room["join_token"], "Bob")

    content = b"# Design Doc\n\nWidget API specification v1"
    r = client.post(
        "/api/v1/rooms/docs-room/documents",
        files={"file": ("design.md", content, "text/markdown")},
        headers=_auth(a["agent_token"]),
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["filename"] == "design.md"
    assert doc["size"] == len(content)

    # B lists documents
    docs = client.get("/api/v1/rooms/docs-room/documents", headers=_auth(b["agent_token"])).json()["data"]
    assert len(docs) == 1
    assert docs[0]["filename"] == "design.md"
    assert docs[0]["uploaded_by"] == a["id"]

    # B downloads — content matches
    r = client.get(f"/api/v1/documents/{doc['id']}/download")
    assert r.status_code == 200
    assert r.content == content


# ---------------------------------------------------------------------------
# 8. Concurrent message ordering
# ---------------------------------------------------------------------------
def test_concurrent_message_ordering(client):
    _login(client)
    room = _create_room(client, "concurrent")
    agents = [_join(client, room["join_token"], f"agent-{i}") for i in range(5)]

    errors = []

    def post_msg(agent, idx):
        try:
            _post(client, "concurrent", f"msg-{idx}", agent["agent_token"])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=post_msg, args=(a, i)) for i, a in enumerate(agents)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"

    msgs = client.get("/api/v1/rooms/concurrent/messages", headers=_auth(agents[0]["agent_token"])).json()["data"]
    assert len(msgs) == 5

    # Monotonically non-decreasing timestamps
    for i in range(1, len(msgs)):
        assert msgs[i]["created_at"] >= msgs[i - 1]["created_at"]


# ---------------------------------------------------------------------------
# 9. Human DM to agent
# ---------------------------------------------------------------------------
def test_human_dm_to_agent(client):
    _login(client)
    room = _create_room(client, "dm-room")
    agent = _join(client, room["join_token"], "DevAgent", "developer")

    r = client.post(f"/ui/api/dm/{agent['id']}", json={"text": "please fix the bug"})
    assert r.status_code == 200
    assert r.json()["priority"] is True

    # Agent polls unread
    data = _unread(client, agent["agent_token"])
    assert len(data["dms"]) == 1
    dm = data["dms"][0]
    assert dm["text"] == "please fix the bug"
    assert dm["priority"] is True
    assert dm["from_name"] == "Human"
    assert dm["from_id"] == "human"


# ---------------------------------------------------------------------------
# 10. Full workflow simulation
# ---------------------------------------------------------------------------
def test_full_workflow_simulation(client):
    _login(client)
    room = _create_room(client, "sprint-task", topic="Build widget API")
    pm = _join(
        client,
        room["join_token"],
        "PM",
        "product-manager",
        {"description": "Product planning", "skills": ["requirements", "coordination"]},
    )
    lead = _join(
        client,
        room["join_token"],
        "Lead",
        "tech-lead",
        {"description": "Architecture", "skills": ["design", "code-review"]},
    )
    dev = _join(
        client,
        room["join_token"],
        "Dev",
        "developer",
        {"description": "Implementation", "skills": ["go", "testing"]},
    )

    # PM posts requirements
    _post(client, "sprint-task", "Build widget CRUD API with validation and tests.", pm["agent_token"])

    # Lead polls, sees PM's message, responds
    lead_data = _unread(client, lead["agent_token"])
    assert any(m["author_name"] == "PM" for m in lead_data["room_messages"])
    _post(client, "sprint-task", "REST design with OpenAPI spec. Standard CRUD.", lead["agent_token"])

    # Dev polls, sees both, responds
    dev_data = _unread(client, dev["agent_token"])
    assert len(dev_data["room_messages"]) >= 2
    _post(client, "sprint-task", "Starting with data models, then handlers.", dev["agent_token"])

    # Lead uploads design doc
    doc_content = b"# Widget API\n- GET /widgets\n- POST /widgets\n- PUT /widgets/:id"
    r = client.post(
        "/api/v1/rooms/sprint-task/documents",
        files={"file": ("api-design.md", doc_content, "text/markdown")},
        headers=_auth(lead["agent_token"]),
    )
    assert r.status_code == 200

    # Status transitions
    r = client.post(
        "/api/v1/rooms/sprint-task/status",
        json={"status": "waiting-for-input"},
        headers=_auth(pm["agent_token"]),
    )
    assert r.status_code == 200

    r = client.post(
        "/api/v1/rooms/sprint-task/status",
        json={"status": "active"},
        headers=_auth(dev["agent_token"]),
    )
    assert r.status_code == 200

    # Dev completes
    _post(client, "sprint-task", "Done. All tests passing.", dev["agent_token"])
    r = client.post(
        "/api/v1/rooms/sprint-task/status",
        json={"status": "completed"},
        headers=_auth(dev["agent_token"]),
    )
    assert r.status_code == 200

    # --- Final assertions ---
    msgs = client.get("/api/v1/rooms/sprint-task/messages", headers=_auth(pm["agent_token"])).json()["data"]
    assert len(msgs) == 4
    assert [m["author_name"] for m in msgs] == ["PM", "Lead", "Dev", "Dev"]
    for i in range(1, len(msgs)):
        assert msgs[i]["created_at"] >= msgs[i - 1]["created_at"]

    docs = client.get("/api/v1/rooms/sprint-task/documents", headers=_auth(pm["agent_token"])).json()["data"]
    assert len(docs) == 1
    assert docs[0]["filename"] == "api-design.md"

    room_data = client.get("/api/v1/rooms/sprint-task", headers=_auth(pm["agent_token"])).json()
    assert room_data["status"] == "completed"
    assert {m["name"] for m in room_data["members"]} == {"PM", "Lead", "Dev"}


# ---------------------------------------------------------------------------
# 11. Human document upload visible to agents
# ---------------------------------------------------------------------------
def test_human_document_upload_visible_to_agents(client):
    _login(client)
    room = _create_room(client, "human-docs")
    agent = _join(client, room["join_token"], "DocReader")

    content = b"Human-uploaded context for the task."
    r = client.post(
        "/ui/api/rooms/human-docs/documents",
        files={"file": ("context.txt", content, "text/plain")},
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["filename"] == "context.txt"

    # Agent sees it in listing
    docs = client.get("/api/v1/rooms/human-docs/documents", headers=_auth(agent["agent_token"])).json()["data"]
    assert len(docs) == 1
    assert docs[0]["filename"] == "context.txt"
    assert docs[0]["uploaded_by"] == "human"

    # Agent downloads — content matches
    r = client.get(f"/api/v1/documents/{doc['id']}/download")
    assert r.status_code == 200
    assert r.content == content


# ---------------------------------------------------------------------------
# 12. Document upload notifies agents via /me/unread
# ---------------------------------------------------------------------------
def test_document_upload_notifies_via_unread(client):
    """When agent A uploads a doc, agent B sees it in /me/unread."""
    _login(client)
    room = _create_room(client, "doc-notify")
    a = _join(client, room["join_token"], "Uploader")
    b = _join(client, room["join_token"], "Watcher")

    # Record timestamp before upload
    time.sleep(0.01)
    before = datetime.now(timezone.utc).isoformat()

    # A uploads a document
    r = client.post(
        "/api/v1/rooms/doc-notify/documents",
        files={"file": ("report.pdf", b"pdf content", "application/pdf")},
        headers=_auth(a["agent_token"]),
    )
    assert r.status_code == 200

    # B polls unread — should see the document
    unread = client.get(
        "/api/v1/me/unread",
        headers=_auth(b["agent_token"]),
        params={"since": before},
    ).json()
    assert len(unread["documents"]) == 1
    assert unread["documents"][0]["filename"] == "report.pdf"
    assert unread["documents"][0]["room"] == "doc-notify"

    # A should NOT see own upload in unread
    unread_a = client.get(
        "/api/v1/me/unread",
        headers=_auth(a["agent_token"]),
        params={"since": before},
    ).json()
    assert len(unread_a["documents"]) == 0


def test_document_upload_wakes_long_poll(client):
    """Document upload should wake a long-polling agent."""
    _login(client)
    room = _create_room(client, "doc-wake")
    a = _join(client, room["join_token"], "DocPoster")
    b = _join(client, room["join_token"], "DocWaiter")

    result = {"unread": None, "elapsed": None}

    def poll_unread():
        start = time.time()
        r = client.get(
            "/api/v1/me/unread",
            headers=_auth(b["agent_token"]),
            params={"wait": "10"},
        )
        result["elapsed"] = time.time() - start
        result["unread"] = r.json()

    t = threading.Thread(target=poll_unread)
    t.start()
    time.sleep(0.5)

    # A uploads while B is long-polling
    client.post(
        "/api/v1/rooms/doc-wake/documents",
        files={"file": ("data.csv", b"a,b,c", "text/csv")},
        headers=_auth(a["agent_token"]),
    )

    t.join(timeout=10)
    assert result["elapsed"] is not None
    assert result["elapsed"] < 5, f"Long-poll took {result['elapsed']:.1f}s, expected < 5s"
    assert len(result["unread"]["documents"]) == 1
    assert result["unread"]["documents"][0]["filename"] == "data.csv"


# ---------------------------------------------------------------------------
# 14. Join response includes room context (topic + documents)
# ---------------------------------------------------------------------------
def test_join_returns_room_context(client):
    _login(client)
    room = _create_room(client, "ctx-room", topic="Design a caching layer for the API")

    # Upload context docs before any agent joins
    client.post(
        "/ui/api/rooms/ctx-room/documents",
        files={"file": ("spec.md", b"# Caching Spec\n\nRequirements...", "text/markdown")},
    )
    client.post(
        "/ui/api/rooms/ctx-room/documents",
        files={"file": ("diagram.png", b"\x89PNG fake", "image/png")},
    )

    # Agent joins — should get topic + document list
    agent = _join(client, room["join_token"], "Dev")
    assert "context" in agent
    assert agent["context"]["topic"] == "Design a caching layer for the API"
    assert len(agent["context"]["documents"]) == 2
    filenames = {d["filename"] for d in agent["context"]["documents"]}
    assert filenames == {"spec.md", "diagram.png"}


def test_join_returns_empty_context_when_no_docs(client):
    _login(client)
    room = _create_room(client, "empty-ctx", topic="Quick sync")
    agent = _join(client, room["join_token"], "Dev")
    assert agent["context"]["topic"] == "Quick sync"
    assert agent["context"]["documents"] == []


def test_join_returns_empty_context_when_no_topic(client):
    _login(client)
    room = _create_room(client, "no-topic")
    agent = _join(client, room["join_token"], "Dev")
    assert agent["context"]["topic"] == ""
    assert agent["context"]["documents"] == []


# ---------------------------------------------------------------------------
# 15. Human sees agent-to-agent DMs within a room (god-mode visibility)
# ---------------------------------------------------------------------------
def test_human_sees_room_member_dms(client):
    _login(client)
    room = _create_room(client, "dm-room")
    other_room = _create_room(client, "other-room")
    a = _join(client, room["join_token"], "Alice")
    b = _join(client, room["join_token"], "Bob")
    outsider = _join(client, other_room["join_token"], "Eve")

    # Agent-to-agent DM within the room
    client.post(f"/api/v1/dm/{b['id']}", json={"text": "private to Bob"}, headers=_auth(a["agent_token"]))
    # Human's own DM to Bob — should NOT appear (human is not in agents table)
    client.post(f"/ui/api/dm/{b['id']}", json={"text": "human to Bob"})
    # Cross-room DM must not leak
    client.post(f"/api/v1/dm/{outsider['id']}", json={"text": "cross-room"}, headers=_auth(a["agent_token"]))

    dms = client.get("/ui/api/rooms/dm-room/dms").json()
    assert len(dms) == 1
    assert dms[0]["from_id"] == a["id"]
    assert dms[0]["to_id"] == b["id"]
    assert dms[0]["text"] == "private to Bob"

    # since-filter excludes the earlier message
    ts = dms[0]["created_at"]
    client.post(f"/api/v1/dm/{a['id']}", json={"text": "reply"}, headers=_auth(b["agent_token"]))
    filtered = client.get("/ui/api/rooms/dm-room/dms", params={"since": ts}).json()
    assert len(filtered) == 1
    assert filtered[0]["text"] == "reply"
