"""Tests for chait server API."""

import asyncio
import sqlite3
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
    server._ui_subscribers.clear()
    server._rate_buckets.clear()
    # Fresh, unbound lock per test: an asyncio.Lock binds to the first event loop
    # that contends it, and each TestClient runs in its own portal loop.
    server._write_lock = asyncio.Lock()
    with TestClient(app=server.app) as c:
        yield c


def _login(client):
    """Login as human, stores session cookie on client.

    Reads server.HUMAN_PASS dynamically: app startup regenerates the default
    ``changeme`` password (see _ensure_human_password), so the current value
    lives on the module global by the time we log in.
    """
    r = client.post(
        "/login",
        data={"user": server.HUMAN_USER, "password": server.HUMAN_PASS},
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


def _db_column_values(table, column):
    """Read a column straight from the on-disk DB.

    Opens a separate sqlite3 connection to server.DB_PATH; the server runs in WAL
    mode, so a fresh reader sees all committed writes. Used to assert what's
    persisted vs. what the API returns/sets in the cookie.
    """
    con = sqlite3.connect(server.DB_PATH)
    try:
        return {row[0] for row in con.execute(f"SELECT {column} FROM {table}")}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TestAuth:
    def test_join_returns_token(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        data = _join(client, room["join_token"], name="agent1")
        assert data["agent_token"].startswith("sk-")
        assert data["id"]
        assert data["name"] == "agent1"

    def test_join_without_name_returns_422(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        r = client.post("/api/v1/join", json={"join_token": room["join_token"], "role": "x"})
        assert r.status_code == 422

    def test_join_invalid_token_returns_403(self, client):
        r = client.post("/api/v1/join", json={"join_token": "bogus", "name": "agent"})
        assert r.status_code == 403

    def test_no_bearer_returns_401(self, client):
        r = client.get("/api/v1/rooms")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, client):
        r = client.get("/api/v1/rooms", headers=_auth("sk-bogus"))
        assert r.status_code == 401

    def test_agent_token_stored_hashed(self, client):
        """agents.agent_token holds the SHA-256 hash, never the returned plaintext."""
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"], name="hasher")
        plaintext = agent["agent_token"]
        stored = _db_column_values("agents", "agent_token")
        assert plaintext not in stored
        assert server._hash_token(plaintext) in stored
        # The plaintext still authenticates (hashed server-side on lookup).
        r = client.get("/api/v1/me", headers=_auth(plaintext))
        assert r.status_code == 200
        assert r.json()["name"] == "hasher"

    def test_session_token_stored_hashed(self, client):
        """sessions.token holds the hash of the cookie value; logout revokes it."""
        _login(client)
        cookie = client.cookies.get("chait_session")
        assert cookie
        stored = _db_column_values("sessions", "token")
        assert cookie not in stored
        assert server._hash_token(cookie) in stored
        # Authenticated UI call works with the plaintext cookie.
        assert client.get("/ui/api/rooms").status_code == 200
        # Logout deletes the hashed row; re-sending the original cookie is rejected.
        client.post("/logout", follow_redirects=False)
        r = client.get("/ui/api/rooms", headers={"Cookie": f"chait_session={cookie}"}, follow_redirects=False)
        assert r.status_code == 303


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
        r = client.put("/api/v1/me/card", json=new_card, headers=_auth(agent["agent_token"]))
        assert r.status_code == 200
        assert r.json()["card"] == new_card

    def test_card_visible_in_room_members(self, client):
        _login(client)
        room = _create_room(client, "r1")
        card = {"skills": ["a", "b"]}
        agent = _join(client, room["join_token"], name="a1", card=card)
        room_data = client.get("/api/v1/rooms/r1", headers=_auth(agent["agent_token"])).json()
        assert len(room_data["members"]) == 1
        assert room_data["members"][0]["card"] == card

    def test_card_persists_in_room_members(self, client):
        _login(client)
        room = _create_room(client, "r1")
        card = {"description": "builder"}
        agent = _join(client, room["join_token"], card=card)
        room_data = client.get("/api/v1/rooms/r1", headers=_auth(agent["agent_token"])).json()
        assert room_data["members"][0]["card"] == card

    def test_update_card_rejects_non_dict_body(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        r = client.put("/api/v1/me/card", json=[1, 2, 3], headers=_auth(agent["agent_token"]))
        assert r.status_code == 422

    def test_update_card_rejects_malformed_json(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        r = client.put(
            "/api/v1/me/card",
            content="{not valid json",
            headers={**_auth(agent["agent_token"]), "Content-Type": "application/json"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------
class TestMe:
    def test_me_returns_identity_and_card(self, client):
        _login(client)
        room = _create_room(client, "test-room")
        card = {"description": "me"}
        agent = _join(client, room["join_token"], name="self", card=card)
        data = client.get("/api/v1/me", headers=_auth(agent["agent_token"])).json()
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
        resp = client.get("/api/v1/rooms", headers=_auth(agent["agent_token"])).json()
        names = [r["name"] for r in resp["data"]]
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
        room_data = client.get("/api/v1/rooms/r1", headers=_auth(agent["agent_token"])).json()
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
            headers=_auth(agent["agent_token"]),
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
            headers=_auth(agent["agent_token"]),
        )
        assert r.status_code == 400

    def test_archive_room_hides_it_from_default_list(self, client):
        _login(client)
        _create_room(client, "r1")
        r = client.post("/ui/api/rooms/r1/archive")
        assert r.status_code == 200
        assert r.json()["archived_at"]
        names = [room["name"] for room in client.get("/ui/api/rooms").json()]
        assert "r1" not in names

    def test_list_archived_rooms(self, client):
        _login(client)
        _create_room(client, "r1")
        _create_room(client, "r2")
        client.post("/ui/api/rooms/r1/archive")
        archived = [room["name"] for room in client.get("/ui/api/rooms?archived=true").json()]
        assert archived == ["r1"]

    def test_unarchive_room_restores_it(self, client):
        _login(client)
        _create_room(client, "r1")
        client.post("/ui/api/rooms/r1/archive")
        r = client.post("/ui/api/rooms/r1/unarchive")
        assert r.status_code == 200
        names = [room["name"] for room in client.get("/ui/api/rooms").json()]
        assert "r1" in names

    def test_archive_nonexistent_room_returns_404(self, client):
        _login(client)
        r = client.post("/ui/api/rooms/no-such-room/archive")
        assert r.status_code == 404

    def test_delete_room_removes_it_and_its_data(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        client.post(
            "/ui/api/rooms/r1/documents",
            files={"file": ("f.txt", b"data", "text/plain")},
        )
        doc_dir = server.DOCS_DIR / room["id"]
        assert doc_dir.exists()

        r = client.delete("/ui/api/rooms/r1")
        assert r.status_code == 200
        assert r.json() == {"status": "deleted", "room": "r1"}

        names = [room["name"] for room in client.get("/ui/api/rooms").json()]
        assert "r1" not in names
        assert not doc_dir.exists()
        # The agent that was in the room is gone too.
        assert client.get("/api/v1/rooms/r1", headers=_auth(agent["agent_token"])).status_code == 401

    def test_delete_nonexistent_room_returns_404(self, client):
        _login(client)
        r = client.delete("/ui/api/rooms/no-such-room")
        assert r.status_code == 404


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
            headers=_auth(agent["agent_token"]),
        )
        assert r.status_code == 200
        assert r.json()["text"] == "hello"

    def test_agent_is_member_after_join(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        resp = client.get("/api/v1/rooms", headers=_auth(agent["agent_token"])).json()
        assert any(r["name"] == "r1" for r in resp["data"])

    def test_get_messages(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["agent_token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "m1"}, headers=h)
        client.post("/api/v1/rooms/r1/messages", json={"text": "m2"}, headers=h)
        resp = client.get("/api/v1/rooms/r1/messages", headers=h).json()
        assert resp["count"] == 2

    def test_get_messages_since_filter(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["agent_token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "old"}, headers=h)
        ts = client.get("/api/v1/rooms/r1/messages", headers=h).json()["data"][0]["created_at"]
        time.sleep(0.02)
        client.post("/api/v1/rooms/r1/messages", json={"text": "new"}, headers=h)
        resp = client.get("/api/v1/rooms/r1/messages", params={"since": ts}, headers=h).json()
        assert resp["count"] == 1
        assert resp["data"][0]["text"] == "new"

    def test_message_author_info(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"], name="alice", role="dev")
        h = _auth(agent["agent_token"])
        client.post("/api/v1/rooms/r1/messages", json={"text": "x"}, headers=h)
        m = client.get("/api/v1/rooms/r1/messages", headers=h).json()["data"][0]
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
            headers=_auth(agent["agent_token"]),
        )
        assert r.status_code == 404

    def test_get_messages_negative_limit_returns_422(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["agent_token"])
        # limit=-1 would make SQLite fetch unbounded; must be rejected.
        assert client.get("/api/v1/rooms/r1/messages", params={"limit": -1}, headers=h).status_code == 422
        assert client.get("/api/v1/rooms/r1/messages", params={"limit": 0}, headers=h).status_code == 422


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
            headers=_auth(a1["agent_token"]),
        )
        assert r.status_code == 200
        assert r.json()["to_id"] == a2["id"]

    def test_get_dms_both_directions(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "m1"}, headers=_auth(a1["agent_token"]))
        client.post(f"/api/v1/dm/{a1['id']}", json={"text": "m2"}, headers=_auth(a2["agent_token"]))
        resp = client.get(f"/api/v1/dm/{a2['id']}", headers=_auth(a1["agent_token"])).json()
        assert resp["count"] == 2

    def test_get_dms_since_filter(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "old"}, headers=_auth(a1["agent_token"]))
        ts = client.get(f"/api/v1/dm/{a2['id']}", headers=_auth(a1["agent_token"])).json()["data"][0]["created_at"]
        time.sleep(0.02)
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "new"}, headers=_auth(a1["agent_token"]))
        resp = client.get(f"/api/v1/dm/{a2['id']}", params={"since": ts}, headers=_auth(a1["agent_token"])).json()
        assert resp["count"] == 1
        assert resp["data"][0]["text"] == "new"

    def test_get_dms_negative_limit_returns_422(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        h = _auth(a1["agent_token"])
        assert client.get(f"/api/v1/dm/{a2['id']}", params={"limit": -1}, headers=h).status_code == 422
        assert client.get(f"/api/v1/dm/{a2['id']}", params={"limit": 0}, headers=h).status_code == 422

    def test_send_dm_same_room_delivered(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        r = client.post(f"/api/v1/dm/{a2['id']}", json={"text": "hi"}, headers=_auth(a1["agent_token"]))
        assert r.status_code == 200
        history = client.get(f"/api/v1/dm/{a1['id']}", headers=_auth(a2["agent_token"])).json()
        assert history["count"] == 1
        assert history["data"][0]["text"] == "hi"

    def test_send_dm_cross_room_returns_404(self, client):
        _login(client)
        room1 = _create_room(client, "r1")
        room2 = _create_room(client, "r2")
        a1 = _join(client, room1["join_token"], name="a1")
        a2 = _join(client, room2["join_token"], name="a2")
        r = client.post(f"/api/v1/dm/{a2['id']}", json={"text": "hi"}, headers=_auth(a1["agent_token"]))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class TestRateLimit:
    def test_message_rate_limit_returns_429(self, client):
        # _rate_buckets is module-level global state shared across tests; clear
        # it so this test deterministically hits the 30/min message cap.
        server._rate_buckets.clear()
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["agent_token"])
        codes = [client.post("/api/v1/rooms/r1/messages", json={"text": "x"}, headers=h).status_code for _ in range(35)]
        assert 429 in codes
        limited = client.post("/api/v1/rooms/r1/messages", json={"text": "x"}, headers=h)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        # Leave global state clean for neighboring tests.
        server._rate_buckets.clear()


# ---------------------------------------------------------------------------
# Unread / long-polling
# ---------------------------------------------------------------------------
class TestUnread:
    def test_unread_returns_room_messages_and_dms(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post("/api/v1/rooms/r1/messages", json={"text": "room msg"}, headers=_auth(a2["agent_token"]))
        client.post(f"/api/v1/dm/{a1['id']}", json={"text": "dm msg"}, headers=_auth(a2["agent_token"]))
        unread = client.get("/api/v1/me/unread", headers=_auth(a1["agent_token"])).json()
        assert len(unread["room_messages"]) >= 1
        assert len(unread["dms"]) >= 1

    def test_unread_wait_returns_immediately_with_messages(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post("/api/v1/rooms/r1/messages", json={"text": "hi"}, headers=_auth(a2["agent_token"]))
        t0 = time.monotonic()
        unread = client.get("/api/v1/me/unread?wait=5", headers=_auth(a1["agent_token"])).json()
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
        unread = client.get(
            "/api/v1/me/unread", params={"wait": 1, "since": future}, headers=_auth(a1["agent_token"])
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
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        r = client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("test.txt", b"hello world", "text/plain")},
            headers=_auth(agent["agent_token"]),
        )
        assert r.status_code == 200
        assert r.json()["filename"] == "test.txt"
        assert r.json()["size"] == 11

    def test_list_documents(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["agent_token"])
        client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("a.txt", b"aaa", "text/plain")},
            headers=h,
        )
        resp = client.get("/api/v1/rooms/r1/documents", headers=h).json()
        assert resp["count"] == 1
        assert resp["data"][0]["filename"] == "a.txt"

    def test_download_document(self, client):
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        up = client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("dl.txt", b"download me", "text/plain")},
            headers=_auth(agent["agent_token"]),
        ).json()
        r = client.get(f"/api/v1/documents/{up['id']}/download")
        assert r.status_code == 200
        assert r.content == b"download me"

    def test_download_document_member_agent(self, client):
        """An agent who is a member of the room can download the document."""
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        up = client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("dl.txt", b"member bytes", "text/plain")},
            headers=_auth(agent["agent_token"]),
        ).json()
        r = client.get(
            f"/api/v1/documents/{up['id']}/download",
            headers=_auth(agent["agent_token"]),
        )
        assert r.status_code == 200
        assert r.content == b"member bytes"

    def test_download_document_cross_room_agent_denied(self, client):
        """An agent in room A cannot download a document from room B (IDOR)."""
        _login(client)
        room_a = _create_room(client, "room-a")
        room_b = _create_room(client, "room-b")
        agent_a = _join(client, room_a["join_token"], name="agent-a")
        agent_b = _join(client, room_b["join_token"], name="agent-b")
        up = client.post(
            "/api/v1/rooms/room-b/documents",
            files={"file": ("secret.txt", b"room b secret", "text/plain")},
            headers=_auth(agent_b["agent_token"]),
        ).json()
        r = client.get(
            f"/api/v1/documents/{up['id']}/download",
            headers=_auth(agent_a["agent_token"]),
        )
        assert r.status_code == 404

    def test_download_document_human_godmode(self, client):
        """A logged-in human can download any document."""
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        up = client.post(
            "/api/v1/rooms/r1/documents",
            files={"file": ("dl.txt", b"human can read", "text/plain")},
            headers=_auth(agent["agent_token"]),
        ).json()
        r = client.get(f"/api/v1/documents/{up['id']}/download")
        assert r.status_code == 200
        assert r.content == b"human can read"


# ---------------------------------------------------------------------------
# Human UI API
# ---------------------------------------------------------------------------
class TestHumanUI:
    def test_ui_requires_session(self, client):
        r = client.get("/ui/api/rooms", follow_redirects=False)
        assert r.status_code == 303

    def test_ui_room_dms_requires_session(self, client):
        r = client.get("/ui/api/rooms/r1/dms", follow_redirects=False)
        assert r.status_code == 303

    def test_ui_room_dms_returns_agent_dms(self, client):
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="a1")
        a2 = _join(client, room["join_token"], name="a2")
        client.post(f"/api/v1/dm/{a2['id']}", json={"text": "hello"}, headers=_auth(a1["agent_token"]))
        dms = client.get("/ui/api/rooms/r1/dms").json()
        assert len(dms) == 1
        assert dms[0]["from_id"] == a1["id"]
        assert dms[0]["to_id"] == a2["id"]
        assert dms[0]["text"] == "hello"

    def test_ui_room_dms_nonexistent_room(self, client):
        _login(client)
        r = client.get("/ui/api/rooms/no-such-room/dms")
        assert r.status_code == 404

    def test_ui_events_requires_session(self, client):
        r = client.get("/ui/api/events", follow_redirects=False)
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
        assert r.status_code == 422

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
# Security hardening
# ---------------------------------------------------------------------------
class TestSecurityHardening:
    def test_login_cookie_secure_when_enabled(self, client, monkeypatch):
        monkeypatch.setattr(server, "SECURE_COOKIES", True)
        r = client.post(
            "/login",
            data={"user": server.HUMAN_USER, "password": server.HUMAN_PASS},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "Secure" in r.headers.get("set-cookie", "")

    def test_login_cookie_not_secure_by_default(self, client, monkeypatch):
        monkeypatch.setattr(server, "SECURE_COOKIES", False)
        r = client.post(
            "/login",
            data={"user": server.HUMAN_USER, "password": server.HUMAN_PASS},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "Secure" not in r.headers.get("set-cookie", "")

    def test_ensure_human_password_replaces_default(self):
        saved = server.HUMAN_PASS
        try:
            server.HUMAN_PASS = "changeme"
            server._ensure_human_password()
            assert server.HUMAN_PASS != "changeme"
            assert server.HUMAN_PASS
        finally:
            server.HUMAN_PASS = saved

    def test_ensure_human_password_keeps_custom(self):
        saved = server.HUMAN_PASS
        try:
            server.HUMAN_PASS = "operator-set-secret"
            server._ensure_human_password()
            assert server.HUMAN_PASS == "operator-set-secret"
        finally:
            server.HUMAN_PASS = saved

    def test_oversized_body_rejected_by_middleware(self, client, monkeypatch):
        # ceiling = MAX_UPLOAD_BYTES + 1_000_000 -> 1_000_000 here
        monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 0)
        big = b"x" * 1_048_576  # 1 MiB > ceiling
        r = client.post("/login", content=big)
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "TOO_LARGE"

    def test_normal_request_passes_middleware(self, client, monkeypatch):
        monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 0)
        r = client.post(
            "/login",
            data={"user": server.HUMAN_USER, "password": server.HUMAN_PASS},
            follow_redirects=False,
        )
        assert r.status_code == 303


class TestSecurityHeaders:
    def test_headers_present_on_normal_response(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        csp = r.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["Permissions-Policy"] == "geolocation=(), microphone=(), camera=()"

    def test_hsts_absent_without_secure_cookies(self, client, monkeypatch):
        monkeypatch.setattr(server, "SECURE_COOKIES", False)
        r = client.get("/health")
        assert "Strict-Transport-Security" not in r.headers

    def test_hsts_present_with_secure_cookies(self, client, monkeypatch):
        monkeypatch.setattr(server, "SECURE_COOKIES", True)
        r = client.get("/health")
        assert r.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


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


# ---------------------------------------------------------------------------
# Unified error envelope
# ---------------------------------------------------------------------------
class TestErrorEnvelope:
    def test_missing_field_envelope(self, client):
        """Raw-body validation raises ApiError -> documented MISSING_FIELD code."""
        _login(client)
        r = client.post("/ui/api/rooms", json={"topic": "x"})
        assert r.status_code == 400
        body = r.json()
        assert body["error"]["code"] == "MISSING_FIELD"
        assert body["error"]["field"] == "name"

    def test_not_found_envelope(self, client):
        """A raw HTTPException(404) is wrapped by the generic handler."""
        _login(client)
        r = client.get("/ui/api/rooms/nope/token")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"

    def test_validation_error_envelope(self, client):
        """FastAPI RequestValidationError (422) uses the same envelope."""
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        r = client.get("/api/v1/rooms/r1/messages", params={"limit": -1}, headers=_auth(agent["agent_token"]))
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_FIELD"

    def test_unauthenticated_ui_redirect_preserved(self, client):
        """CRITICAL: require_human's 303 must stay a real redirect, not JSON.

        Breaking this sends unauthenticated humans a JSON error instead of the
        login page, breaking the whole UI (and the UI test suite).
        """
        r = client.get("/ui/api/rooms/any/token", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"
        assert "error" not in (r.text or "")


# ---------------------------------------------------------------------------
# Check-then-insert races (idempotency contract)
# ---------------------------------------------------------------------------
class TestConcurrencyRaces:
    def test_join_same_name_is_idempotent(self, client):
        """Re-joining same name+room reuses the row (same id) but ROTATES the token.

        Tokens are stored hashed, so the original plaintext can't be returned on
        re-join; the server issues a fresh one bound to the same identity and
        invalidates the old one.
        """
        _login(client)
        room = _create_room(client, "r1")
        a1 = _join(client, room["join_token"], name="dup-agent")
        a2 = _join(client, room["join_token"], name="dup-agent")
        assert a1["id"] == a2["id"]
        # Exactly one agent row for (name, room) — no duplicate. Authenticate with
        # the current (rotated) token.
        members = client.get("/api/v1/rooms/r1", headers=_auth(a2["agent_token"])).json()["members"]
        assert [m["name"] for m in members].count("dup-agent") == 1
        # Rotation invalidates the previous token.
        assert a2["agent_token"] != a1["agent_token"]
        assert client.get("/api/v1/me", headers=_auth(a1["agent_token"])).status_code == 401

    def test_duplicate_room_returns_existing_once(self, client):
        """Creating a room with an existing name returns the existing one."""
        _login(client)
        first = _create_room(client, "dup-room")
        second = _create_room(client, "dup-room")
        assert second["id"] == first["id"]
        assert second["existing"] is True
        rooms = client.get("/ui/api/rooms").json()
        assert [r["name"] for r in rooms].count("dup-room") == 1


# ---------------------------------------------------------------------------
# Write serialization behind _write_lock
# ---------------------------------------------------------------------------
class TestWriteSerialization:
    """Guards the single-connection write-isolation fix.

    NOTE: TestClass::test_delete_room_removes_it_and_its_data (in TestRooms)
    is the atomicity regression guard for ui_delete_room's 4-DELETE sequence:
    it asserts a deleted room leaves no rooms/agents/messages/documents behind.
    """

    def test_write_lock_is_asyncio_lock(self):
        assert isinstance(server._write_lock, asyncio.Lock)

    def test_concurrent_message_posts_all_persist_once(self, client):
        """20 concurrent posts to one room must each persist exactly once.

        Fires the requests concurrently in the TestClient's portal event loop
        (asyncio.to_thread over the blocking client), so they genuinely contend
        _write_lock. With writes serialized, each commit() flushes only its own
        INSERT: the final count is exactly 20, every body appears once, and no
        request errors (a lost commit or a deadlock would fail this).
        """
        _login(client)
        _create_room(client, "race")
        n = 20

        def _post(i):
            return client.post("/ui/api/rooms/race/messages", json={"text": f"m{i}"})

        async def _fire():
            return await asyncio.gather(*[asyncio.to_thread(_post, i) for i in range(n)])

        results = asyncio.run(_fire())
        assert all(r.status_code == 200 for r in results)
        texts = sorted(m["text"] for m in client.get("/ui/api/rooms/race/messages").json())
        assert texts == sorted(f"m{i}" for i in range(n))


# ---------------------------------------------------------------------------
# Monotonic timestamps (_now)
# ---------------------------------------------------------------------------
class TestMonotonicNow:
    """Guards the strictly-increasing, unique timestamp source.

    The `created_at > since` cursor skips rows that share a timestamp and
    regresses on a backward wall-clock step, so _now() must never repeat or
    go backwards. monkeypatch.setattr snapshots server._last_ts and restores
    it at teardown so these tests don't leak state to neighbors.
    """

    def test_now_is_strictly_increasing_and_unique(self, monkeypatch):
        monkeypatch.setattr(server, "_last_ts", server._last_ts)
        vals = [server._now() for _ in range(1000)]
        assert all(a < b for a, b in zip(vals, vals[1:]))  # strictly increasing
        assert len(set(vals)) == len(vals)  # all unique

    def test_now_bumps_on_same_instant(self, monkeypatch):
        # Freeze the wall clock so datetime.now() returns a fixed value; delegate
        # fromisoformat to the real implementation for the +1µs bump.
        frozen = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        class _FrozenDatetime:
            @staticmethod
            def now(tz=None):
                return frozen

            @staticmethod
            def fromisoformat(s):
                return datetime.fromisoformat(s)

        monkeypatch.setattr(server, "datetime", _FrozenDatetime)
        monkeypatch.setattr(server, "_last_ts", "")
        first = server._now()
        second = server._now()
        assert second > first  # microsecond bump, not a repeat

    def test_cursor_advances_without_losing_messages(self, client):
        """Paginate limit=1, advancing `since` to the last created_at seen.

        This is the documented client loop; with monotonic timestamps every
        message gets a distinct created_at so none are skipped past.
        """
        _login(client)
        room = _create_room(client, "r1")
        agent = _join(client, room["join_token"])
        h = _auth(agent["agent_token"])
        # Baseline cursor from the same monotonic clock the server uses; every
        # message posted after this gets a strictly greater created_at. A truthy
        # `since` selects the forward-ASC pagination branch (empty/None returns
        # the latest N DESC instead).
        since = server._now()
        n = 8
        for i in range(n):
            assert client.post("/api/v1/rooms/r1/messages", json={"text": f"m{i}"}, headers=h).status_code == 200

        seen = []
        while True:
            data = client.get("/api/v1/rooms/r1/messages", params={"limit": 1, "since": since}, headers=h).json()[
                "data"
            ]
            if not data:
                break
            seen.extend(m["text"] for m in data)
            since = data[-1]["created_at"]

        assert seen == [f"m{i}" for i in range(n)]  # every message retrieved, none lost
