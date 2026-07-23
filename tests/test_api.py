"""Tests for chait server API."""
import sys
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


def _register(client, name="agent1", role="tester", card=None):
    body = {"name": name, "role": role}
    if card is not None:
        body["card"] = card
    r = client.post("/api/v1/register", json=body)
    assert r.status_code == 200
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_room(client, name="test-room", topic=""):
    r = client.post("/api/v1/rooms", json={"name": name, "topic": topic})
    assert r.status_code == 200
    return r.json()


def _login(client):
    """Login as human, stores session cookie on client."""
    r = client.post(
        "/login",
        data={"user": "admin", "password": "changeme"},
        follow_redirects=False,
    )
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TestAuth:
    def test_register_returns_token(self, client):
        data = _register(client)
        assert data["token"].startswith("sk-")
        assert data["id"]
        assert data["name"] == "agent1"

    def test_register_without_name_returns_400(self, client):
        r = client.post("/api/v1/register", json={"role": "x"})
        assert r.status_code == 400

    def test_no_bearer_returns_401(self, client):
        r = client.get("/api/v1/rooms")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, client):
        r = client.get("/api/v1/rooms", headers=_auth("sk-bogus"))
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Agent cards
# ---------------------------------------------------------------------------
class TestAgentCards:
    def test_register_with_card(self, client):
        card = {"description": "test agent", "skills": ["python"]}
        data = _register(client, card=card)
        assert data["card"] == card

    def test_update_card(self, client):
        agent = _register(client)
        new_card = {"description": "updated", "skills": ["go", "rust"]}
        r = client.put("/api/v1/me/card", json=new_card, headers=_auth(agent["token"]))
        assert r.status_code == 200
        assert r.json()["card"] == new_card

    def test_list_agents_with_cards(self, client):
        card = {"skills": ["a", "b"]}
        _register(client, name="a1", card=card)
        agents = client.get("/api/v1/agents").json()
        assert len(agents) == 1
        assert agents[0]["card"] == card

    def test_card_persists_in_room_members(self, client):
        card = {"description": "builder"}
        agent = _register(client, card=card)
        _create_room(client, "r1")
        client.post("/api/v1/rooms/r1/join", headers=_auth(agent["token"]))
        room = client.get("/api/v1/rooms/r1", headers=_auth(agent["token"])).json()
        assert room["members"][0]["card"] == card


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------
class TestMe:
    def test_me_returns_identity_and_card(self, client):
        card = {"description": "me"}
        agent = _register(client, name="self", card=card)
        data = client.get("/api/v1/me", headers=_auth(agent["token"])).json()
        assert data["name"] == "self"
        assert data["card"] == card


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------
class TestRooms:
    def test_create_room(self, client):
        data = _create_room(client, "myroom", "a topic")
        assert data["name"] == "myroom"
        assert data["status"] == "active"
        assert data["id"]

    def test_create_room_without_name_returns_400(self, client):
        r = client.post("/api/v1/rooms", json={"topic": "x"})
        assert r.status_code == 400

    def test_duplicate_room_returns_existing(self, client):
        first = _create_room(client, "dup")
        second = _create_room(client, "dup")
        assert second["id"] == first["id"]
        assert second.get("existing") is True

    def test_list_rooms_only_joined(self, client):
        agent = _register(client)
        _create_room(client, "joined")
        _create_room(client, "not-joined")
        client.post("/api/v1/rooms/joined/join", headers=_auth(agent["token"]))
        rooms = client.get("/api/v1/rooms", headers=_auth(agent["token"])).json()
        names = [r["name"] for r in rooms]
        assert "joined" in names
        assert "not-joined" not in names

    def test_join_room(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        r = client.post("/api/v1/rooms/r1/join", headers=_auth(agent["token"]))
        assert r.status_code == 200
        assert r.json()["joined"] == "r1"

    def test_join_nonexistent_room_returns_404(self, client):
        agent = _register(client)
        r = client.post("/api/v1/rooms/nope/join", headers=_auth(agent["token"]))
        assert r.status_code == 404

    def test_get_room_with_members(self, client):
        agent = _register(client, name="bob")
        _create_room(client, "r1")
        client.post("/api/v1/rooms/r1/join", headers=_auth(agent["token"]))
        room = client.get("/api/v1/rooms/r1", headers=_auth(agent["token"])).json()
        assert room["name"] == "r1"
        assert len(room["members"]) == 1
        assert room["members"][0]["name"] == "bob"

    def test_set_room_status(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        r = client.post(
            "/api/v1/rooms/r1/status",
            json={"status": "completed"},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_invalid_status_returns_400(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        r = client.post(
            "/api/v1/rooms/r1/status",
            json={"status": "nonsense"},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
class TestMessages:
    def test_post_message(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        r = client.post(
            "/api/v1/rooms/r1/messages",
            json={"text": "hello"},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 200
        assert r.json()["text"] == "hello"

    def test_auto_join_on_message(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        client.post(
            "/api/v1/rooms/r1/messages",
            json={"text": "hi"},
            headers=_auth(agent["token"]),
        )
        rooms = client.get("/api/v1/rooms", headers=_auth(agent["token"])).json()
        assert any(r["name"] == "r1" for r in rooms)

    def test_get_messages(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        h = _auth(agent["token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "m1"}, headers=h)
        client.post("/api/v1/rooms/r1/messages", json={"text": "m2"}, headers=h)
        msgs = client.get("/api/v1/rooms/r1/messages", headers=h).json()
        assert len(msgs) == 2

    def test_get_messages_since_filter(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        h = _auth(agent["token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "old"}, headers=h)
        ts = client.get("/api/v1/rooms/r1/messages", headers=h).json()[0]["created_at"]
        time.sleep(0.02)
        client.post("/api/v1/rooms/r1/messages", json={"text": "new"}, headers=h)
        filtered = client.get("/api/v1/rooms/r1/messages", params={"since": ts}, headers=h).json()
        assert len(filtered) == 1
        assert filtered[0]["text"] == "new"

    def test_message_author_info(self, client):
        agent = _register(client, name="alice", role="dev")
        _create_room(client, "r1")
        h = _auth(agent["token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "x"}, headers=h)
        m = client.get("/api/v1/rooms/r1/messages", headers=h).json()[0]
        assert m["author_id"] == agent["id"]
        assert m["author_name"] == "alice"
        assert m["author_role"] == "dev"

    def test_post_to_nonexistent_room_returns_404(self, client):
        agent = _register(client)
        r = client.post(
            "/api/v1/rooms/nope/messages",
            json={"text": "x"},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DMs
# ---------------------------------------------------------------------------
class TestDMs:
    def test_send_dm(self, client):
        a1 = _register(client, name="a1")
        a2 = _register(client, name="a2")
        r = client.post(
            f"/api/v1/dm/{a2['id']}",
            json={"text": "hey"},
            headers=_auth(a1["token"]),
        )
        assert r.status_code == 200
        assert r.json()["to_id"] == a2["id"]

    def test_get_dms_both_directions(self, client):
        a1 = _register(client, name="a1")
        a2 = _register(client, name="a2")
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "m1"}, headers=_auth(a1["token"]))
        client.post(f"/api/v1/dm/{a1['id']}", json={"text": "m2"}, headers=_auth(a2["token"]))
        dms = client.get(f"/api/v1/dm/{a2['id']}", headers=_auth(a1["token"])).json()
        assert len(dms) == 2

    def test_get_dms_since_filter(self, client):
        a1 = _register(client, name="a1")
        a2 = _register(client, name="a2")
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "old"}, headers=_auth(a1["token"]))
        ts = client.get(f"/api/v1/dm/{a2['id']}", headers=_auth(a1["token"])).json()[0]["created_at"]
        time.sleep(0.02)
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "new"}, headers=_auth(a1["token"]))
        filtered = client.get(
            f"/api/v1/dm/{a2['id']}", params={"since": ts}, headers=_auth(a1["token"])
        ).json()
        assert len(filtered) == 1
        assert filtered[0]["text"] == "new"


# ---------------------------------------------------------------------------
# Unread / long-polling
# ---------------------------------------------------------------------------
class TestUnread:
    def test_unread_returns_room_messages_and_dms(self, client):
        a1 = _register(client, name="a1")
        a2 = _register(client, name="a2")
        _create_room(client, "r1")
        client.post("/api/v1/rooms/r1/join", headers=_auth(a1["token"]))
        client.post("/api/v1/rooms/r1/messages", json={"text": "room msg"}, headers=_auth(a2["token"]))
        client.post(f"/api/v1/dm/{a1['id']}", json={"text": "dm msg"}, headers=_auth(a2["token"]))
        unread = client.get("/api/v1/me/unread", headers=_auth(a1["token"])).json()
        assert len(unread["room_messages"]) >= 1
        assert len(unread["dms"]) >= 1

    def test_unread_wait_returns_immediately_with_messages(self, client):
        a1 = _register(client, name="a1")
        a2 = _register(client, name="a2")
        _create_room(client, "r1")
        client.post("/api/v1/rooms/r1/join", headers=_auth(a1["token"]))
        client.post("/api/v1/rooms/r1/messages", json={"text": "hi"}, headers=_auth(a2["token"]))
        t0 = time.monotonic()
        unread = client.get("/api/v1/me/unread?wait=5", headers=_auth(a1["token"])).json()
        elapsed = time.monotonic() - t0
        assert elapsed < 2
        assert len(unread["room_messages"]) >= 1

    def test_unread_wait_times_out_with_no_messages(self, client):
        a1 = _register(client)
        future = datetime.now(timezone.utc).isoformat()
        time.sleep(0.02)
        t0 = time.monotonic()
        unread = client.get(
            "/api/v1/me/unread", params={"wait": 1, "since": future}, headers=_auth(a1["token"])
        ).json()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.9
        assert unread["room_messages"] == []
        assert unread["dms"] == []


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class TestDocuments:
    def test_upload_document(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        r = client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("test.txt", b"hello world", "text/plain")},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 200
        assert r.json()["filename"] == "test.txt"
        assert r.json()["size"] == 11

    def test_list_documents(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        h = _auth(agent["token"])
        client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("a.txt", b"aaa", "text/plain")},
            headers=h,
        )
        docs = client.get("/api/v1/rooms/r1/documents", headers=h).json()
        assert len(docs) == 1
        assert docs[0]["filename"] == "a.txt"

    def test_download_document(self, client):
        agent = _register(client)
        _create_room(client, "r1")
        up = client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("dl.txt", b"download me", "text/plain")},
            headers=_auth(agent["token"]),
        ).json()
        r = client.get(f"/api/v1/documents/{up['id']}/download")
        assert r.status_code == 200
        assert r.content == b"download me"


# ---------------------------------------------------------------------------
# Human UI API
# ---------------------------------------------------------------------------
class TestHumanUI:
    def test_ui_requires_session(self, client):
        r = client.get("/ui/api/rooms", follow_redirects=False)
        assert r.status_code == 303

    def test_ui_send_message_priority(self, client):
        _login(client)
        _create_room(client, "r1")
        r = client.post("/ui/api/rooms/r1/messages", json={"text": "urgent"})
        assert r.status_code == 200
        assert r.json()["priority"] is True

    def test_ui_send_dm_priority(self, client):
        _login(client)
        agent = _register(client)
        r = client.post(f"/ui/api/dm/{agent['id']}", json={"text": "hey"})
        assert r.status_code == 200
        assert r.json()["priority"] is True

    def test_ui_upload_document(self, client):
        _login(client)
        _create_room(client, "r1")
        r = client.post(
            "/ui/api/rooms/r1/documents",
            files={"file": ("ui.txt", b"from ui", "text/plain")},
        )
        assert r.status_code == 200
        assert r.json()["filename"] == "ui.txt"

    def test_ui_invalid_login(self, client):
        r = client.post(
            "/login", data={"user": "admin", "password": "wrong"}, follow_redirects=False
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------
class TestInstructions:
    def test_instructions_returns_markdown_with_base_url(self, client):
        r = client.get("/api/v1/instructions")
        assert r.status_code == 200
        assert "text/markdown" in r.headers["content-type"]
        assert "chait API" in r.text
        assert "http://testserver/api/v1" in r.text
