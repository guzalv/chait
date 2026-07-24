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
    server._rate_buckets.clear()
    with TestClient(app=server.app) as c:
        yield c


def _login(client):
    """Login as human, stores session cookie on client."""
    r = client.post(
        "/login",
        data={"user": "admin", "password": "changeme"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _create_room(client, name, topic=""):
    """Create room via UI API (requires human session)."""
    r = client.post("/ui/api/rooms", json={"name": name, "topic": topic})
    assert r.status_code == 200
    return r.json()


def _join(client, join_token, name="Agent", role="agent", card=None):
    """Join a room as an agent using join token."""
    body = {"join_token": join_token, "name": name, "role": role}
    if card:
        body["card"] = card
    r = client.post("/api/v1/join", json=body)
    assert r.status_code == 200
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TestAuth:
    def test_join_returns_token(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        data = _join(client, room["join_token"], name="agent1")
        assert data["token"].startswith("sk-")
        assert data["id"]
        assert data["name"] == "agent1"

    def test_join_without_name_returns_400(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        r = client.post("/api/v1/join", json={"join_token": room["join_token"], "role": "x"})
        assert r.status_code == 400

    def test_join_invalid_token_returns_403(self, client):
        r = client.post("/api/v1/join", json={"join_token": "bogus", "name": "agent"})
        assert r.status_code == 403

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
    def test_join_with_card(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        card = {"description": "test agent", "skills": ["python"]}
        data = _join(client, room["join_token"], card=card)
        assert data["card"] == card

    def test_update_card(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        agent = _join(client, room["join_token"])
        new_card = {"description": "updated", "skills": ["go", "rust"]}
        r = client.put("/api/v1/me/card", json=new_card, headers=_auth(agent["token"]))
        assert r.status_code == 200
        assert r.json()["card"] == new_card

    def test_card_visible_in_room_members(self, client):
        _login(client)
        room = _create_room(client, "r1")
        card = {"skills": ["a", "b"]}
        agent = _join(client, room["join_token"], name="a1", card=card)
        room_data = client.get("/api/v1/rooms/r1", headers=_auth(agent["token"])).json()
        assert len(room_data["members"]) == 1
        assert room_data["members"][0]["card"] == card

    def test_card_persists_in_room_members(self, client):
        _login(client)
        room = _create_room(client, "r1")
        card = {"description": "builder"}
        agent = _join(client, room["join_token"], card=card)
        room_data = client.get("/api/v1/rooms/r1", headers=_auth(agent["token"])).json()
        assert room_data["members"][0]["card"] == card


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------
class TestMe:
    def test_me_returns_identity_and_card(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        card = {"description": "me"}
        agent = _join(client, room["join_token"], name="self", card=card)
        data = client.get("/api/v1/me", headers=_auth(agent["token"])).json()
        assert data["name"] == "self"
        assert data["card"] == card


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------
class TestRooms:
    def test_create_room(self, client):
        _login(client)
        data = _create_room(client, "myroom", "a topic")
        assert data["name"] == "myroom"
        assert data["status"] == "active"
        assert data["id"]
        assert data["join_token"]

    def test_create_room_without_name_returns_400(self, client):
        _login(client)
        r = client.post("/ui/api/rooms", json={"topic": "x"})
        assert r.status_code == 400

    def test_duplicate_room_returns_existing(self, client):
        _login(client)
        first = _create_room(client, "dup")
        second = _create_room(client, "dup")
        assert second["id"] == first["id"]
        assert second.get("existing") is True

    def test_list_rooms_only_joined(self, client):
        _login(client)
        room_joined = _create_room(client, "joined")
        _create_room(client, "not-joined")
        agent = _join(client, room_joined["join_token"])
        rooms = client.get("/api/v1/rooms", headers=_auth(agent["token"])).json()
        names = [r["name"] for r in rooms]
        assert "joined" in names
        assert "not-joined" not in names

    def test_join_room_via_token(self, client):
        _login(client)
        room = _create_room(client, "r1")
        data = _join(client, room["join_token"], name="bob")
        assert data["room"] == "r1"
        assert data["name"] == "bob"

    def test_get_room_with_members(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"], name="bob")
        room_data = client.get("/api/v1/rooms/r1", headers=_auth(agent["token"])).json()
        assert room_data["name"] == "r1"
        assert len(room_data["members"]) == 1
        assert room_data["members"][0]["name"] == "bob"

    def test_set_room_status(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        r = client.post(
            "/api/v1/rooms/r1/status",
            json={"status": "completed"},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_invalid_status_returns_400(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
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
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        r = client.post(
            "/api/v1/rooms/r1/messages",
            json={"text": "hello"},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 200
        assert r.json()["text"] == "hello"

    def test_agent_is_member_after_join(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        rooms = client.get("/api/v1/rooms", headers=_auth(agent["token"])).json()
        assert any(r["name"] == "r1" for r in rooms)

    def test_get_messages(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "m1"}, headers=h)
        client.post("/api/v1/rooms/r1/messages", json={"text": "m2"}, headers=h)
        msgs = client.get("/api/v1/rooms/r1/messages", headers=h).json()
        assert len(msgs) == 2

    def test_get_messages_since_filter(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "old"}, headers=h)
        ts = client.get("/api/v1/rooms/r1/messages", headers=h).json()[0]["created_at"]
        time.sleep(0.02)
        client.post("/api/v1/rooms/r1/messages", json={"text": "new"}, headers=h)
        filtered = client.get("/api/v1/rooms/r1/messages", params={"since": ts}, headers=h).json()
        assert len(filtered) == 1
        assert filtered[0]["text"] == "new"

    def test_message_author_info(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"], name="alice", role="dev")
        h = _auth(agent["token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "x"}, headers=h)
        m = client.get("/api/v1/rooms/r1/messages", headers=h).json()[0]
        assert m["author_id"] == agent["id"]
        assert m["author_name"] == "alice"
        assert m["author_role"] == "dev"

    def test_post_to_nonexistent_room_returns_404(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
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
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        r = client.post(
            f"/api/v1/dm/{a2['id']}",
            json={"text": "hey"},
            headers=_auth(a1["token"]),
        )
        assert r.status_code == 200
        assert r.json()["to_id"] == a2["id"]

    def test_get_dms_both_directions(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "m1"}, headers=_auth(a1["token"]))
        client.post(f"/api/v1/dm/{a1['id']}", json={"text": "m2"}, headers=_auth(a2["token"]))
        dms = client.get(f"/api/v1/dm/{a2['id']}", headers=_auth(a1["token"])).json()
        assert len(dms) == 2

    def test_get_dms_since_filter(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "old"}, headers=_auth(a1["token"]))
        ts = client.get(f"/api/v1/dm/{a2['id']}", headers=_auth(a1["token"])).json()[0]["created_at"]
        time.sleep(0.02)
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "new"}, headers=_auth(a1["token"]))
        filtered = client.get(f"/api/v1/dm/{a2['id']}", params={"since": ts}, headers=_auth(a1["token"])).json()
        assert len(filtered) == 1
        assert filtered[0]["text"] == "new"


# ---------------------------------------------------------------------------
# Unread / long-polling
# ---------------------------------------------------------------------------
class TestUnread:
    def test_unread_returns_room_messages_and_dms(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post("/api/v1/rooms/r1/messages", json={"text": "room msg"}, headers=_auth(a2["token"]))
        client.post(f"/api/v1/dm/{a1['id']}", json={"text": "dm msg"}, headers=_auth(a2["token"]))
        unread = client.get("/api/v1/me/unread", headers=_auth(a1["token"])).json()
        assert len(unread["room_messages"]) >= 1
        assert len(unread["dms"]) >= 1

    def test_unread_wait_returns_immediately_with_messages(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post("/api/v1/rooms/r1/messages", json={"text": "hi"}, headers=_auth(a2["token"]))
        t0 = time.monotonic()
        unread = client.get("/api/v1/me/unread?wait=5", headers=_auth(a1["token"])).json()
        elapsed = time.monotonic() - t0
        assert elapsed < 2
        assert len(unread["room_messages"]) >= 1

    def test_unread_wait_times_out_with_no_messages(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"])
        future = datetime.now(timezone.utc).isoformat()
        time.sleep(0.02)
        t0 = time.monotonic()
        unread = client.get("/api/v1/me/unread", params={"wait": 1, "since": future}, headers=_auth(a1["token"])).json()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.9
        assert unread["room_messages"] == []
        assert unread["dms"] == []


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class TestDocuments:
    def test_upload_document(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        r = client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("test.txt", b"hello world", "text/plain")},
            headers=_auth(agent["token"]),
        )
        assert r.status_code == 200
        assert r.json()["filename"] == "test.txt"
        assert r.json()["size"] == 11

    def test_list_documents(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
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
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
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
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
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
        r = client.post("/login", data={"user": "admin", "password": "wrong"}, follow_redirects=False)
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# API room creation (CHAIT_TOKEN)
# ---------------------------------------------------------------------------
class TestAPICreateRoom:
    def _make_api_token(self, client):
        """Generate an API token via UI (requires human session)."""
        r = client.post("/ui/api/tokens")
        assert r.status_code == 200
        return r.json()["token"]

    def test_create_room_with_api_token(self, client):
        _login(client)
        token = self._make_api_token(client)
        r = client.post(
            "/api/v1/rooms",
            json={"name": "api-room", "topic": "test topic"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "api-room"
        assert data["topic"] == "test topic"
        assert data["join_token"].startswith("chait-")
        assert data["status"] == "active"

    def test_create_room_invalid_token(self, client):
        r = client.post(
            "/api/v1/rooms",
            json={"name": "x"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401

    def test_create_room_no_auth_header(self, client):
        r = client.post("/api/v1/rooms", json={"name": "x"})
        assert r.status_code == 401

    def test_create_room_missing_name(self, client):
        _login(client)
        token = self._make_api_token(client)
        r = client.post(
            "/api/v1/rooms",
            json={"topic": "no name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    def test_create_room_duplicate_returns_existing(self, client):
        _login(client)
        token = self._make_api_token(client)
        h = {"Authorization": f"Bearer {token}"}
        first = client.post("/api/v1/rooms", json={"name": "dup"}, headers=h).json()
        second = client.post("/api/v1/rooms", json={"name": "dup"}, headers=h).json()
        assert second["id"] == first["id"]
        assert second.get("existing") is True

    def test_created_room_is_joinable(self, client):
        _login(client)
        token = self._make_api_token(client)
        room = client.post(
            "/api/v1/rooms",
            json={"name": "joinable"},
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        agent = _join(client, room["join_token"], name="bot")
        assert agent["room"] == "joinable"

    def test_revoked_token_rejected(self, client):
        _login(client)
        data = client.post("/ui/api/tokens").json()
        client.delete(f"/ui/api/tokens/{data['id']}")
        r = client.post(
            "/api/v1/rooms",
            json={"name": "x"},
            headers={"Authorization": f"Bearer {data['token']}"},
        )
        assert r.status_code == 401


class TestAPITokenManagement:
    def test_generate_token(self, client):
        _login(client)
        r = client.post("/ui/api/tokens")
        assert r.status_code == 200
        data = r.json()
        assert data["token"].startswith("chait-api-")
        assert data["id"]

    def test_list_tokens(self, client):
        _login(client)
        client.post("/ui/api/tokens")
        client.post("/ui/api/tokens")
        tokens = client.get("/ui/api/tokens").json()
        assert len(tokens) == 2

    def test_revoke_token(self, client):
        _login(client)
        data = client.post("/ui/api/tokens").json()
        r = client.delete(f"/ui/api/tokens/{data['id']}")
        assert r.status_code == 200
        assert r.json()["revoked"] is True
        tokens = client.get("/ui/api/tokens").json()
        assert len(tokens) == 0

    def test_requires_session(self, client):
        r = client.post("/ui/api/tokens", follow_redirects=False)
        assert r.status_code == 303
        r = client.get("/ui/api/tokens", follow_redirects=False)
        assert r.status_code == 303


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
