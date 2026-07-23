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
    with TestClient(app=server.app) as c:
        yield c


def _register(client, name="agent", role="agent", card=None):
    body = {"name": name, "role": role}
    if card is not None:
        body["card"] = card
    r = client.post("/api/v1/register", json=body)
    assert r.status_code == 200
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _room(client, name, topic=""):
    r = client.post("/api/v1/rooms", json={"name": name, "topic": topic})
    assert r.status_code == 200
    return r.json()


def _join(client, room, token):
    r = client.post(f"/api/v1/rooms/{room}/join", headers=_auth(token))
    assert r.status_code == 200


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


def _login(client):
    r = client.post(
        "/login",
        data={"user": "admin", "password": "changeme"},
        follow_redirects=False,
    )
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# 1. Multi-agent room conversation
# ---------------------------------------------------------------------------
def test_multi_agent_room_conversation(client):
    a = _register(client, "Alice", "designer", {"skills": ["ui"]})
    b = _register(client, "Bob", "developer", {"skills": ["go"]})
    c = _register(client, "Carol", "tester", {"skills": ["pytest"]})

    _room(client, "project-x")
    for ag in [a, b, c]:
        _join(client, "project-x", ag["token"])

    # A posts
    _post(client, "project-x", "Here are the mockups", a["token"])

    # B and C poll unread — both see A's message
    for ag in [b, c]:
        data = _unread(client, ag["token"])
        assert any(
            m["text"] == "Here are the mockups" and m["room"] == "project-x"
            for m in data["room_messages"]
        )

    # B replies
    _post(client, "project-x", "Looks good, implementing", b["token"])

    # A and C see B's reply
    for ag in [a, c]:
        data = _unread(client, ag["token"])
        assert any(m["text"] == "Looks good, implementing" for m in data["room_messages"])

    # All messages in correct order with correct authors
    msgs = client.get(
        "/api/v1/rooms/project-x/messages", headers=_auth(a["token"])
    ).json()
    assert len(msgs) == 2
    assert msgs[0]["author_name"] == "Alice"
    assert msgs[1]["author_name"] == "Bob"
    assert msgs[0]["created_at"] <= msgs[1]["created_at"]


# ---------------------------------------------------------------------------
# 2. Long-poll notification delivery
# ---------------------------------------------------------------------------
def test_long_poll_notification_delivery(client):
    a = _register(client, "Alice")
    b = _register(client, "Bob")
    _room(client, "poll-room")
    _join(client, "poll-room", a["token"])
    _join(client, "poll-room", b["token"])

    result = {}

    def poll():
        try:
            t0 = time.monotonic()
            r = client.get(
                "/api/v1/me/unread", params={"wait": 10}, headers=_auth(b["token"])
            )
            result["elapsed"] = time.monotonic() - t0
            result["data"] = r.json()
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=poll)
    t.start()
    time.sleep(1)  # let poll enter wait state

    _post(client, "poll-room", "wake up!", a["token"])
    t.join(timeout=8)

    assert not t.is_alive(), "Long-poll thread still running"
    assert "error" not in result, f"Poll error: {result.get('error')}"
    assert result["elapsed"] < 5, f"Took {result['elapsed']:.1f}s, expected < 5"
    assert any(m["text"] == "wake up!" for m in result["data"]["room_messages"])


# ---------------------------------------------------------------------------
# 3. Human god-mode priority
# ---------------------------------------------------------------------------
def test_human_god_mode_priority(client):
    a = _register(client, "Alice", "dev")
    b = _register(client, "Bob", "dev")
    _room(client, "priority-room")
    _join(client, "priority-room", a["token"])
    _join(client, "priority-room", b["token"])

    # Agent A posts normal message
    _post(client, "priority-room", "agent message", a["token"])

    # Human posts priority message (requires session cookie)
    _login(client)
    r = client.post(
        "/ui/api/rooms/priority-room/messages", json={"text": "urgent from human"}
    )
    assert r.status_code == 200
    assert r.json()["priority"] is True

    # B polls unread — sees both
    data = _unread(client, b["token"])
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
    a = _register(client, "Alice")
    b = _register(client, "Bob")
    c = _register(client, "Carol")

    # A sends DM to B
    r = client.post(
        f"/api/v1/dm/{b['id']}", json={"text": "private to Bob"}, headers=_auth(a["token"])
    )
    assert r.status_code == 200

    # B sees the DM
    b_data = _unread(client, b["token"])
    assert any(
        d["text"] == "private to Bob" and d["from_id"] == a["id"]
        for d in b_data["dms"]
    )

    # C does NOT see the DM
    c_data = _unread(client, c["token"])
    assert len(c_data["dms"]) == 0

    # B reads DM history with A
    history = client.get(f"/api/v1/dm/{a['id']}", headers=_auth(b["token"])).json()
    assert len(history) == 1
    assert history[0]["text"] == "private to Bob"
    assert history[0]["from_id"] == a["id"]
    assert history[0]["to_id"] == b["id"]


# ---------------------------------------------------------------------------
# 5. Room lifecycle (status transitions)
# ---------------------------------------------------------------------------
def test_room_lifecycle_status_transitions(client):
    agent = _register(client)
    data = _room(client, "lifecycle-room")
    assert data["status"] == "active"
    h = _auth(agent["token"])

    transitions = ["waiting-for-input", "active", "completed"]
    for status in transitions:
        r = client.post(
            "/api/v1/rooms/lifecycle-room/status", json={"status": status}, headers=h
        )
        assert r.status_code == 200
        assert r.json()["status"] == status

        # Verify via GET
        _join(client, "lifecycle-room", agent["token"])
        room = client.get("/api/v1/rooms/lifecycle-room", headers=h).json()
        assert room["status"] == status

    # Invalid status → 400
    r = client.post(
        "/api/v1/rooms/lifecycle-room/status", json={"status": "nonsense"}, headers=h
    )
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
    agent = _register(client, "CardAgent", "engineer", card)
    _room(client, "card-room")
    _join(client, "card-room", agent["token"])
    h = _auth(agent["token"])

    # Card present in room member info
    room = client.get("/api/v1/rooms/card-room", headers=h).json()
    member = room["members"][0]
    assert member["card"] == card

    # Update card
    new_card = {"description": "Senior engineer", "skills": ["python", "react", "go"]}
    r = client.put("/api/v1/me/card", json=new_card, headers=h)
    assert r.status_code == 200

    # Updated card in room
    room = client.get("/api/v1/rooms/card-room", headers=h).json()
    assert room["members"][0]["card"] == new_card

    # Card in agents list
    agents = client.get("/api/v1/agents").json()
    match = next(a for a in agents if a["id"] == agent["id"])
    assert match["card"] == new_card


# ---------------------------------------------------------------------------
# 7. Document sharing
# ---------------------------------------------------------------------------
def test_document_sharing(client):
    a = _register(client, "Alice")
    b = _register(client, "Bob")
    _room(client, "docs-room")
    _join(client, "docs-room", a["token"])
    _join(client, "docs-room", b["token"])

    content = b"# Design Doc\n\nWidget API specification v1"
    r = client.post(
        "/api/v1/rooms/docs-room/documents",
        files={"file": ("design.md", content, "text/markdown")},
        headers=_auth(a["token"]),
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["filename"] == "design.md"
    assert doc["size"] == len(content)

    # B lists documents
    docs = client.get(
        "/api/v1/rooms/docs-room/documents", headers=_auth(b["token"])
    ).json()
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
    agents = [_register(client, f"agent-{i}") for i in range(5)]
    _room(client, "concurrent")
    for a in agents:
        _join(client, "concurrent", a["token"])

    errors = []

    def post_msg(agent, idx):
        try:
            _post(client, "concurrent", f"msg-{idx}", agent["token"])
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=post_msg, args=(a, i)) for i, a in enumerate(agents)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"

    msgs = client.get(
        "/api/v1/rooms/concurrent/messages", headers=_auth(agents[0]["token"])
    ).json()
    assert len(msgs) == 5

    # Monotonically non-decreasing timestamps
    for i in range(1, len(msgs)):
        assert msgs[i]["created_at"] >= msgs[i - 1]["created_at"]


# ---------------------------------------------------------------------------
# 9. Human DM to agent
# ---------------------------------------------------------------------------
def test_human_dm_to_agent(client):
    agent = _register(client, "DevAgent", "developer")
    _login(client)

    r = client.post(
        f"/ui/api/dm/{agent['id']}", json={"text": "please fix the bug"}
    )
    assert r.status_code == 200
    assert r.json()["priority"] is True

    # Agent polls unread
    data = _unread(client, agent["token"])
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
    pm = _register(
        client,
        "PM",
        "product-manager",
        {"description": "Product planning", "skills": ["requirements", "coordination"]},
    )
    lead = _register(
        client,
        "Lead",
        "tech-lead",
        {"description": "Architecture", "skills": ["design", "code-review"]},
    )
    dev = _register(
        client,
        "Dev",
        "developer",
        {"description": "Implementation", "skills": ["go", "testing"]},
    )

    _room(client, "sprint-task", topic="Build widget API")
    for ag in [pm, lead, dev]:
        _join(client, "sprint-task", ag["token"])

    # PM posts requirements
    _post(client, "sprint-task", "Build widget CRUD API with validation and tests.", pm["token"])

    # Lead polls, sees PM's message, responds
    lead_data = _unread(client, lead["token"])
    assert any(m["author_name"] == "PM" for m in lead_data["room_messages"])
    _post(client, "sprint-task", "REST design with OpenAPI spec. Standard CRUD.", lead["token"])

    # Dev polls, sees both, responds
    dev_data = _unread(client, dev["token"])
    assert len(dev_data["room_messages"]) >= 2
    _post(client, "sprint-task", "Starting with data models, then handlers.", dev["token"])

    # Lead uploads design doc
    doc_content = b"# Widget API\n- GET /widgets\n- POST /widgets\n- PUT /widgets/:id"
    r = client.post(
        "/api/v1/rooms/sprint-task/documents",
        files={"file": ("api-design.md", doc_content, "text/markdown")},
        headers=_auth(lead["token"]),
    )
    assert r.status_code == 200

    # Status transitions
    r = client.post(
        "/api/v1/rooms/sprint-task/status",
        json={"status": "waiting-for-input"},
        headers=_auth(pm["token"]),
    )
    assert r.status_code == 200

    r = client.post(
        "/api/v1/rooms/sprint-task/status",
        json={"status": "active"},
        headers=_auth(dev["token"]),
    )
    assert r.status_code == 200

    # Dev completes
    _post(client, "sprint-task", "Done. All tests passing.", dev["token"])
    r = client.post(
        "/api/v1/rooms/sprint-task/status",
        json={"status": "completed"},
        headers=_auth(dev["token"]),
    )
    assert r.status_code == 200

    # --- Final assertions ---
    msgs = client.get(
        "/api/v1/rooms/sprint-task/messages", headers=_auth(pm["token"])
    ).json()
    assert len(msgs) == 4
    assert [m["author_name"] for m in msgs] == ["PM", "Lead", "Dev", "Dev"]
    for i in range(1, len(msgs)):
        assert msgs[i]["created_at"] >= msgs[i - 1]["created_at"]

    docs = client.get(
        "/api/v1/rooms/sprint-task/documents", headers=_auth(pm["token"])
    ).json()
    assert len(docs) == 1
    assert docs[0]["filename"] == "api-design.md"

    room = client.get(
        "/api/v1/rooms/sprint-task", headers=_auth(pm["token"])
    ).json()
    assert room["status"] == "completed"
    assert {m["name"] for m in room["members"]} == {"PM", "Lead", "Dev"}


# ---------------------------------------------------------------------------
# 11. Human document upload visible to agents
# ---------------------------------------------------------------------------
def test_human_document_upload_visible_to_agents(client):
    agent = _register(client, "DocReader")
    _room(client, "human-docs")
    _join(client, "human-docs", agent["token"])
    _login(client)

    content = b"Human-uploaded context for the task."
    r = client.post(
        "/ui/api/rooms/human-docs/documents",
        files={"file": ("context.txt", content, "text/plain")},
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["filename"] == "context.txt"

    # Agent sees it in listing
    docs = client.get(
        "/api/v1/rooms/human-docs/documents", headers=_auth(agent["token"])
    ).json()
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
    a = _register(client, "Uploader")
    b = _register(client, "Watcher")
    _room(client, "doc-notify")
    _join(client, "doc-notify", a["token"])
    _join(client, "doc-notify", b["token"])

    # Record timestamp before upload
    import time; time.sleep(0.01)
    before = datetime.now(timezone.utc).isoformat()

    # A uploads a document
    r = client.post(
        "/api/v1/rooms/doc-notify/documents",
        files={"file": ("report.pdf", b"pdf content", "application/pdf")},
        headers=_auth(a["token"]),
    )
    assert r.status_code == 200

    # B polls unread — should see the document
    unread = client.get(
        "/api/v1/me/unread", headers=_auth(b["token"]),
        params={"since": before},
    ).json()
    assert len(unread["documents"]) == 1
    assert unread["documents"][0]["filename"] == "report.pdf"
    assert unread["documents"][0]["room"] == "doc-notify"

    # A should NOT see own upload in unread
    unread_a = client.get(
        "/api/v1/me/unread", headers=_auth(a["token"]),
        params={"since": before},
    ).json()
    assert len(unread_a["documents"]) == 0


def test_document_upload_wakes_long_poll(client):
    """Document upload should wake a long-polling agent."""
    a = _register(client, "DocPoster")
    b = _register(client, "DocWaiter")
    _room(client, "doc-wake")
    _join(client, "doc-wake", a["token"])
    _join(client, "doc-wake", b["token"])

    result = {"unread": None, "elapsed": None}

    def poll_unread():
        start = time.time()
        r = client.get(
            "/api/v1/me/unread", headers=_auth(b["token"]),
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
        headers=_auth(a["token"]),
    )

    t.join(timeout=10)
    assert result["elapsed"] is not None
    assert result["elapsed"] < 5, f"Long-poll took {result['elapsed']:.1f}s, expected < 5s"
    assert len(result["unread"]["documents"]) == 1
    assert result["unread"]["documents"][0]["filename"] == "data.csv"
