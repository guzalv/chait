"""Selenium UI tests for chait dashboard.

Starts a local server on a random port, runs tests against it, tears down.
No external server needed.
"""

import http.cookiejar
import json
import multiprocessing
import os
import socket
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

import pytest
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

USER = "uitestuser"
PASS = "uitestpass"


def wait(driver, cond, t=10):
    """Small WebDriverWait wrapper to cut boilerplate on explicit waits."""
    return WebDriverWait(driver, t).until(cond)


def _free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _run_server(port, data_dir):
    """Run chait server in a subprocess."""
    os.environ["CHAIT_PORT"] = str(port)
    os.environ["CHAIT_HUMAN_USER"] = USER
    os.environ["CHAIT_HUMAN_PASS"] = PASS
    os.environ["CHAIT_DATA_DIR"] = data_dir
    os.environ["CHAIT_TOKEN_TTL_HOURS"] = "0"  # no token expiry in tests
    os.environ["CHAIT_RATE_LIMIT_MULTIPLIER"] = "100"  # effectively disable rate limits
    import uvicorn

    # Import after setting env so config picks it up
    import importlib, sys

    # Ensure fresh module load with new env
    if "server" in sys.modules:
        del sys.modules["server"]
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from server import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# ── API helpers ───────────────────────────────────────────────────────────


def _api_post(base, path, body, token=None):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _login_session(base):
    """Login as human and return a cookie-aware opener."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    login_data = urllib.parse.urlencode({"user": USER, "password": PASS}).encode()
    opener.open(f"{base}/login", login_data)
    return opener


def _ui_api_post(opener, base, path, body):
    """POST to UI API endpoint with session cookie."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req) as r:
        return json.loads(r.read())


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def server_url():
    """Start a chait server on a random port, yield its URL, then kill it."""
    port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="chait-test-")
    proc = multiprocessing.Process(target=_run_server, args=(port, data_dir), daemon=True)
    proc.start()
    url = f"http://127.0.0.1:{port}"
    # Wait for server to be ready
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{url}/api/v1/instructions")
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("Server failed to start")
    yield url
    proc.kill()
    proc.join(timeout=3)


@pytest.fixture(scope="session")
def test_data(server_url):
    """Create a room + agent + messages via API for tests to use."""
    room_name = f"uitest-{int(time.time())}"

    # 1. Login as human (capture session cookie)
    opener = _login_session(server_url)

    # 2. Create room via UI API (uses session cookie)
    room_data = _ui_api_post(
        opener,
        server_url,
        "/ui/api/rooms",
        {
            "name": room_name,
            "topic": "UI test topic",
        },
    )
    join_token = room_data["join_token"]

    # 3. Join as agent via join token
    agent = _api_post(
        server_url,
        "/api/v1/join",
        {
            "join_token": join_token,
            "name": "TestBot",
            "role": "tester",
            "card": {
                "description": "Bot for UI tests",
                "skills": ["selenium", "pytest"],
            },
        },
    )
    token = agent["agent_token"]

    # 4. Post messages using agent token
    _api_post(server_url, f"/api/v1/rooms/{room_name}/messages", {"text": "Hello from TestBot"}, token=token)
    _api_post(server_url, f"/api/v1/rooms/{room_name}/messages", {"text": "Second message"}, token=token)

    return {"room": room_name, "agent": agent, "agent_token": token}


def _make_chrome(mobile=False):
    """Create a Chrome driver, optionally with mobile emulation.

    Centralises option-building so the session `driver`, the function-scoped
    `fresh_driver`, and `mobile_driver` fixtures don't each duplicate it.
    """
    opts = webdriver.ChromeOptions()
    # Try snap chromium, fall back to system
    snap_bin = "/snap/chromium/current/usr/lib/chromium-browser/chrome"
    if os.path.exists(snap_bin):
        opts.binary_location = snap_bin
    for a in [
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        f"--user-data-dir={tempfile.mkdtemp(dir='/tmp')}",
    ]:
        opts.add_argument(a)
    if mobile:
        opts.add_experimental_option(
            "mobileEmulation",
            {
                "deviceMetrics": {"width": 390, "height": 844, "pixelRatio": 3.0},
                "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            },
        )
    else:
        opts.add_argument("--window-size=1920,1080")
    snap_driver = "/snap/bin/chromium.chromedriver"
    if os.path.exists(snap_driver):
        svc = webdriver.ChromeService(executable_path=snap_driver)
        return webdriver.Chrome(service=svc, options=opts)
    return webdriver.Chrome(options=opts)


@pytest.fixture(scope="session")
def driver():
    d = _make_chrome()
    d.implicitly_wait(2)
    yield d
    d.quit()


@pytest.fixture
def fresh_driver():
    """Function-scoped browser with its own session, for tests that must not
    disturb the shared session-scoped `driver` (e.g. logout, which invalidates
    the session cookie)."""
    d = _make_chrome()
    d.implicitly_wait(2)
    yield d
    d.quit()


def _do_login(d, server_url):
    """Log a driver in via the web login form."""
    d.get(f"{server_url}/login")
    WebDriverWait(d, 10).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
    d.find_element(By.CSS_SELECTOR, 'input[name="user"]').send_keys(USER)
    d.find_element(By.CSS_SELECTOR, 'input[name="password"]').send_keys(PASS)
    d.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    WebDriverWait(d, 10).until(lambda drv: "/login" not in drv.current_url)


def _reset_selection(d, server_url):
    """Force a clean 'no room selected' load on a shared driver.

    The dashboard persists the selected room in sessionStorage and auto-restores
    it on load (hiding #no-room / the mobile sidebar). A prior test may have left
    a room selected, so tests that assert the unselected state must reset first.
    The unique query string busts same-URL fragment navigation so init() re-runs
    with no saved room and no hash; the server ignores the query string.
    """
    d.get(server_url)
    d.execute_script("sessionStorage.removeItem('chait_room')")
    d.get(f"{server_url}/?reset={int(time.time() * 1000)}")


@pytest.fixture(scope="session")
def logged_in(driver, server_url):
    """Log in once for all tests that need a session."""
    _do_login(driver, server_url)
    return True


# ── Login flow ────────────────────────────────────────────────────────────


class TestLogin:
    def test_login_page_renders(self, driver, server_url):
        driver.get(f"{server_url}/login")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
        assert driver.find_element(By.CSS_SELECTOR, 'input[name="user"]')
        assert driver.find_element(By.CSS_SELECTOR, 'input[name="password"]')
        assert driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')

    def test_valid_login_redirects(self, driver, server_url):
        driver.get(f"{server_url}/login")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
        driver.find_element(By.CSS_SELECTOR, 'input[name="user"]').send_keys(USER)
        driver.find_element(By.CSS_SELECTOR, 'input[name="password"]').send_keys(PASS)
        driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        WebDriverWait(driver, 10).until(lambda d: "/login" not in d.current_url)
        assert "/login" not in driver.current_url

    def test_invalid_login_shows_error(self, driver, server_url):
        driver.get(f"{server_url}/login")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
        driver.find_element(By.CSS_SELECTOR, 'input[name="user"]').send_keys("wrong")
        driver.find_element(By.CSS_SELECTOR, 'input[name="password"]').send_keys("wrong")
        driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        wait(driver, lambda d: "invalid" in d.page_source.lower())
        assert "invalid" in driver.page_source.lower()

    def test_dashboard_redirects_without_session(self, fresh_driver, server_url):
        # Own browser: deleting cookies to simulate "no session" must NOT log out
        # the shared session-scoped `driver` (whose login fixture won't re-run).
        fresh_driver.get(server_url)
        fresh_driver.delete_all_cookies()
        fresh_driver.get(server_url)
        wait(fresh_driver, EC.url_contains("/login"))
        assert "/login" in fresh_driver.current_url


# ── Dashboard layout ─────────────────────────────────────────────────────


class TestDashboard:
    def test_sidebar_shows_rooms(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        # Atomic read: the SSE stream re-renders .room-item on any event, so
        # querying then reading .text in a separate call can hit stale handles.
        names = driver.execute_script(
            "return Array.from(document.querySelectorAll('.room-item')).map(i => i.innerText.split('\\n')[0]).join(' ')"
        )
        assert test_data["room"] in names

    def test_rooms_have_status_badges(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-status")))
        badges = driver.find_elements(By.CSS_SELECTOR, ".room-status")
        assert len(badges) > 0
        # Atomic read: an SSE re-render between find_elements and the class read
        # invalidates the badge handles (StaleElementReferenceException).
        classes = driver.execute_script(
            "return Array.from(document.querySelectorAll('.room-status')).map(b => b.className).join(' ')"
        )
        assert "status-active" in classes

    def test_right_panel_sections(self, driver, server_url, logged_in):
        driver.get(server_url)
        panel = driver.find_element(By.ID, "right-panel")
        panel_text = panel.text.lower()
        assert "room members" in panel_text
        assert "documents" in panel_text

    def test_select_room_placeholder(self, driver, server_url, logged_in):
        # Order-independent: clear any room a prior test persisted so the app
        # loads showing the placeholder rather than auto-restoring a room.
        _reset_selection(driver, server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "no-room")))
        wait(driver, lambda d: "Select a room" in d.find_element(By.ID, "no-room").text)
        el = driver.find_element(By.ID, "no-room")
        assert "Select a room" in el.text


# ── Room interaction ─────────────────────────────────────────────────────


class TestRoomInteraction:
    def test_clicking_room_shows_header(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        items = driver.find_elements(By.CSS_SELECTOR, ".room-item")
        for item in items:
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "room-title")))
        title = driver.find_element(By.ID, "room-title").text
        assert test_data["room"] in title

    def test_messages_area_populates(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#messages .msg")))
        msgs = driver.find_elements(By.CSS_SELECTOR, "#messages .msg")
        assert len(msgs) >= 2

    def test_room_topic_displays(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "room-topic")))
        topic = driver.find_element(By.ID, "room-topic").text
        assert "UI test topic" in topic


# ── Agent cards ──────────────────────────────────────────────────────────


class TestAgentCards:
    def _select_room(self, driver, server_url, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".agent-card")))

    def test_agent_card_shows_name_role_desc(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        cards = driver.find_elements(By.CSS_SELECTOR, ".agent-card")
        assert len(cards) >= 1
        # Atomic read: .agent-card is re-rendered by the SSE stream, so reading
        # .text from a previously-captured handle can raise stale-element.
        card_text = driver.execute_script("return document.querySelector('.agent-card').innerText")
        assert "TestBot" in card_text
        assert "tester" in card_text

    def test_agent_card_shows_skills(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        # Atomic read: .agent-card is re-rendered by the SSE stream between the
        # query and the .text read, invalidating the captured handles.
        skill_text = driver.execute_script(
            "return Array.from(document.querySelectorAll('.agent-card')).map(c => c.innerText).join(' ')"
        )
        assert "selenium" in skill_text or "pytest" in skill_text

    def test_agent_card_has_dm_button(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        WebDriverWait(driver, 5).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".agent-card .dm-btn")) >= 1)


# ── Human messaging ──────────────────────────────────────────────────────


class TestHumanMessaging:
    def _select_room(self, driver, server_url, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#messages .msg")))

    def test_send_via_button(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        textarea = driver.find_element(By.ID, "msg-input")
        textarea.send_keys("Button send test")
        driver.find_element(By.CSS_SELECTOR, "#input-area .btn").click()
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), "Button send test"))
        # Atomic read: SSE re-renders #messages .msg, so collect text in one call.
        msgs_text = driver.execute_script(
            "return Array.from(document.querySelectorAll('#messages .msg')).map(m => m.innerText)"
        )
        assert any("Button send test" in t for t in msgs_text)

    def test_send_via_enter(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        textarea = driver.find_element(By.ID, "msg-input")
        textarea.send_keys("Enter send test")
        textarea.send_keys(Keys.RETURN)
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), "Enter send test"))
        # Atomic read: SSE re-renders #messages .msg, so collect text in one call.
        msgs_text = driver.execute_script(
            "return Array.from(document.querySelectorAll('#messages .msg')).map(m => m.innerText)"
        )
        assert any("Enter send test" in t for t in msgs_text)

    def test_sent_message_has_human_author_and_priority(self, driver, server_url, logged_in, test_data):
        # Order-independent: send our own human message rather than relying on
        # messages left behind by earlier tests. UI messages are always authored
        # as Human/[god] with priority (server.py inserts author_role='god',
        # priority=1), so this message alone satisfies the assertions below.
        self._select_room(driver, server_url, test_data)
        unique = f"human-prio-{int(time.time() * 1000)}"
        textarea = driver.find_element(By.ID, "msg-input")
        textarea.send_keys(unique)
        textarea.send_keys(Keys.RETURN)
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), unique))
        # Atomic read: SSE re-renders #messages .msg, so collect text in one call.
        msgs_text = driver.execute_script(
            "return Array.from(document.querySelectorAll('#messages .msg')).map(m => m.innerText)"
        )
        human_msgs = [t for t in msgs_text if "Human" in t and "[god]" in t]
        assert len(human_msgs) > 0
        # At least one should have priority badge
        priority_msgs = [t for t in msgs_text if "PRIORITY" in t]
        assert len(priority_msgs) > 0


# ── File upload ──────────────────────────────────────────────────────────


class TestFileUpload:
    def test_upload_button_exists(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "input-area")))
        labels = driver.find_elements(By.CSS_SELECTOR, "#input-area label")
        assert any("Upload" in l.text for l in labels)

    def test_hidden_file_input(self, driver, server_url, logged_in, test_data):
        # Order-independent: navigate to the dashboard ourselves rather than
        # relying on the previous test leaving the driver there. #file-input is
        # in the static DOM (inside the hidden room-view), so no room is needed.
        driver.get(server_url)
        fi = wait(driver, EC.presence_of_element_located((By.ID, "file-input")))
        assert fi.get_attribute("type") == "file"


# ── DM modal ─────────────────────────────────────────────────────────────


class TestDMModal:
    def _open_dm(self, driver, server_url, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".dm-btn")))
        btn.click()
        WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By.ID, "dm-modal").value_of_css_property("display") != "none"
        )

    def test_dm_modal_opens_with_agent_name(self, driver, server_url, logged_in, test_data):
        self._open_dm(driver, server_url, test_data)
        name_el = driver.find_element(By.ID, "dm-target-name")
        assert "TestBot" in name_el.text

    def test_dm_modal_has_controls(self, driver, server_url, logged_in, test_data):
        self._open_dm(driver, server_url, test_data)
        assert driver.find_element(By.ID, "dm-input")
        btns = driver.find_elements(By.CSS_SELECTOR, "#dm-modal .btn")
        texts = [b.text for b in btns]
        assert "Send DM" in texts
        assert "Close" in texts

    def test_close_dismisses_modal(self, driver, server_url, logged_in, test_data):
        self._open_dm(driver, server_url, test_data)
        for btn in driver.find_elements(By.CSS_SELECTOR, "#dm-modal .btn"):
            if "Close" in btn.text:
                btn.click()
                break
        wait(driver, lambda d: d.find_element(By.ID, "dm-modal").value_of_css_property("display") == "none")
        modal = driver.find_element(By.ID, "dm-modal")
        assert modal.value_of_css_property("display") == "none"


# ── Live updates ─────────────────────────────────────────────────────────


class TestLiveUpdates:
    def test_sent_message_appears_after_send(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#messages .msg")))
        unique = f"live-{int(time.time())}"
        textarea = driver.find_element(By.ID, "msg-input")
        textarea.send_keys(unique)
        textarea.send_keys(Keys.RETURN)
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), unique))
        assert unique in driver.find_element(By.ID, "messages").text

    def test_api_message_appears_on_poll(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#messages .msg")))
        unique = f"api-{int(time.time())}"
        _api_post(
            server_url, f"/api/v1/rooms/{test_data['room']}/messages", {"text": unique}, token=test_data["agent_token"]
        )
        # SSE should deliver near-instantly; wait (don't sleep) with a generous
        # timeout for CI jitter.
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), unique), t=15)
        assert unique in driver.find_element(By.ID, "messages").text


# ── SSE live-update stream (raw HTTP, no browser needed) ───────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urlopen() follows redirects by default; disable that so a 303-to-login
    surfaces as an HTTPError instead of silently landing on the login page."""

    def redirect_request(self, *args, **kwargs):
        return None


class TestSSEEndpoint:
    def test_requires_login(self, server_url):
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(f"{server_url}/ui/api/events")
            assert False, "expected an error response for unauthenticated request"
        except urllib.error.HTTPError as e:
            assert e.code == 303

    def test_delivers_event_on_new_message(self, server_url, test_data):
        opener = _login_session(server_url)
        resp = opener.open(f"{server_url}/ui/api/events")
        assert resp.headers.get_content_type() == "text/event-stream"

        result = {}

        def read_one_event():
            try:
                for raw_line in resp:
                    line = raw_line.decode().strip()
                    if line.startswith("data:"):
                        result["data"] = line
                        return
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=read_one_event, daemon=True)
        t.start()
        time.sleep(0.5)  # let the subscriber queue register server-side
        unique = f"sse-{int(time.time())}"
        _api_post(
            server_url, f"/api/v1/rooms/{test_data['room']}/messages", {"text": unique}, token=test_data["agent_token"]
        )
        t.join(timeout=5)
        resp.close()

        assert not t.is_alive(), "SSE reader thread never received an event"
        assert "error" not in result, f"SSE read error: {result.get('error')}"
        assert result.get("data") == "data: message"


# ── Mobile viewport tests ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def mobile_driver():
    d = _make_chrome(mobile=True)
    d.implicitly_wait(2)
    yield d
    d.quit()


@pytest.fixture(scope="session")
def mobile_logged_in(mobile_driver, server_url):
    _do_login(mobile_driver, server_url)
    return True


def _mobile_select_room(mobile_driver, server_url, test_data):
    """Navigate to dashboard and tap the test room on mobile."""
    mobile_driver.get(server_url)
    WebDriverWait(mobile_driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
    for item in mobile_driver.find_elements(By.CSS_SELECTOR, ".room-item"):
        if test_data["room"] in item.text:
            item.click()
            break
    WebDriverWait(mobile_driver, 5).until(EC.visibility_of_element_located((By.ID, "room-view")))


class TestMobile:
    def test_mobile_sidebar_visible_on_load(self, mobile_driver, server_url, mobile_logged_in):
        # Order-independent: a prior mobile test may have selected a room (which
        # hides the sidebar and is persisted/auto-restored). Reset to no-room.
        _reset_selection(mobile_driver, server_url)
        WebDriverWait(mobile_driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        assert mobile_driver.find_element(By.ID, "sidebar").is_displayed()

    def test_mobile_onclick_not_broken(self, mobile_driver, server_url, mobile_logged_in):
        """Verify room onclick attributes contain valid JS (no quote truncation)."""
        mobile_driver.get(server_url)
        WebDriverWait(mobile_driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        # Atomic read: .room-item is re-rendered by SSE, so grab every onclick
        # attribute in one call instead of reading from per-item handles.
        onclicks = mobile_driver.execute_script(
            "return Array.from(document.querySelectorAll('.room-item')).map(i => i.getAttribute('onclick'))"
        )
        for onclick in onclicks:
            assert onclick is not None, "onclick missing"
            assert "selectRoom(" in onclick, f"onclick truncated: {onclick!r}"
            assert onclick.endswith(")"), f"onclick not complete: {onclick!r}"

    def test_mobile_room_tap_opens_room(self, mobile_driver, server_url, mobile_logged_in, test_data):
        _mobile_select_room(mobile_driver, server_url, test_data)
        assert not mobile_driver.find_element(By.ID, "sidebar").is_displayed()
        assert mobile_driver.find_element(By.ID, "main").is_displayed()
        assert test_data["room"] in mobile_driver.find_element(By.ID, "room-title").text

    def test_mobile_back_button_returns_to_sidebar(self, mobile_driver, server_url, mobile_logged_in, test_data):
        _mobile_select_room(mobile_driver, server_url, test_data)
        mobile_driver.find_element(By.CSS_SELECTOR, ".mobile-back").click()
        wait(mobile_driver, EC.visibility_of_element_located((By.ID, "sidebar")))
        assert mobile_driver.find_element(By.ID, "sidebar").is_displayed()

    def test_mobile_no_js_errors(self, mobile_driver, server_url, mobile_logged_in, test_data):
        """No JS errors after navigating rooms on mobile."""
        _mobile_select_room(mobile_driver, server_url, test_data)
        logs = mobile_driver.get_log("browser")
        errors = [l for l in logs if l["level"] == "SEVERE" and "favicon" not in l["message"]]
        assert errors == [], f"JS errors: {errors}"


# ── Modal dismiss tests ──────────────────────────────────────────────────


class TestModalDismiss:
    def test_escape_closes_new_room_modal(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        driver.find_element(By.XPATH, "//button[contains(text(),'+ Room')]").click()
        modal = driver.find_element(By.ID, "new-room-modal")
        wait(driver, lambda d: modal.value_of_css_property("display") != "none")
        assert modal.value_of_css_property("display") != "none"
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        wait(driver, lambda d: modal.value_of_css_property("display") == "none")
        assert modal.value_of_css_property("display") == "none"

    def test_click_outside_closes_modal(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        driver.find_element(By.XPATH, "//button[contains(text(),'+ Room')]").click()
        modal = driver.find_element(By.ID, "new-room-modal")
        wait(driver, lambda d: modal.value_of_css_property("display") != "none")
        assert modal.value_of_css_property("display") != "none"
        driver.execute_script("document.getElementById('new-room-modal').click()")
        wait(driver, lambda d: modal.value_of_css_property("display") == "none")
        assert modal.value_of_css_property("display") == "none"


# ── Room creation flow ───────────────────────────────────────────────────


class TestRoomCreation:
    def test_create_room_shows_token(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        driver.find_element(By.XPATH, "//button[contains(text(),'+ Room')]").click()
        wait(driver, EC.visibility_of_element_located((By.ID, "new-room-name")))
        name_input = driver.find_element(By.ID, "new-room-name")
        room_name = f"ui-create-{int(time.time())}"
        name_input.send_keys(room_name)
        driver.find_element(By.ID, "create-room-btn").click()
        WebDriverWait(driver, 5).until(lambda d: d.find_element(By.ID, "new-room-token").text.startswith("chait-"))
        assert driver.find_element(By.ID, "new-room-token").text.startswith("chait-")

    def test_created_room_in_sidebar(self, driver, server_url, logged_in):
        # Order-independent: create our own uniquely-named room via the modal
        # rather than depending on a room/modal left behind by a prior test.
        driver.get(server_url)
        wait(driver, EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        driver.find_element(By.XPATH, "//button[contains(text(),'+ Room')]").click()
        wait(driver, EC.visibility_of_element_located((By.ID, "new-room-name")))
        room_name = f"ui-sidebar-{int(time.time() * 1000)}"
        driver.find_element(By.ID, "new-room-name").send_keys(room_name)
        driver.find_element(By.ID, "create-room-btn").click()
        wait(driver, lambda d: d.find_element(By.ID, "new-room-token").text.startswith("chait-"))
        for btn in driver.find_elements(By.CSS_SELECTOR, "#new-room-modal .btn"):
            if "Close" in btn.text:
                btn.click()
                break
        # Atomic read: SSE re-renders .room-item, so collect text in one call.
        wait(
            driver,
            lambda d: any(
                room_name in t
                for t in d.execute_script(
                    "return Array.from(document.querySelectorAll('.room-item')).map(i => i.innerText)"
                )
            ),
        )
        items_text = driver.execute_script(
            "return Array.from(document.querySelectorAll('.room-item')).map(i => i.innerText)"
        )
        assert any(room_name in t for t in items_text)


# ── Connection status ────────────────────────────────────────────────────


class TestConnectionStatus:
    def test_live_indicator_visible(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "conn-status")))
        # The badge now starts neutral ("Connecting") and flips to "Live" once the
        # SSE stream opens, so wait for that instead of asserting immediately.
        WebDriverWait(driver, 10).until(EC.text_to_be_present_in_element((By.ID, "conn-status"), "Live"))
        assert driver.find_element(By.ID, "conn-status").text == "Live"


# ── Room status control ──────────────────────────────────────────────────


class TestRoomStatusControl:
    def _select_room(self, driver, server_url, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "room-status-select")))

    def test_status_dropdown_exists(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        sel = driver.find_element(By.ID, "room-status-select")
        assert sel.tag_name == "select"
        options = [o.get_attribute("value") for o in sel.find_elements(By.TAG_NAME, "option")]
        assert "active" in options and "completed" in options

    def test_change_status(self, driver, server_url, logged_in, test_data):
        from selenium.webdriver.support.ui import Select

        self._select_room(driver, server_url, test_data)
        Select(driver.find_element(By.ID, "room-status-select")).select_by_value("waiting-for-input")

        # Atomic read: SSE re-renders .room-item, so collect text in one call.
        def _room_status_contains(word):
            return lambda d: any(
                word in t.lower()
                for t in d.execute_script(
                    "return Array.from(document.querySelectorAll('.room-item')).map(i => i.innerText)"
                )
                if test_data["room"] in t
            )

        wait(driver, _room_status_contains("waiting"))
        items_text = driver.execute_script(
            "return Array.from(document.querySelectorAll('.room-item')).map(i => i.innerText)"
        )
        assert any("waiting" in t.lower() for t in items_text if test_data["room"] in t)
        # Reset — wait until the sidebar reflects the reset so a later test that
        # asserts on this room's status isn't racing an in-flight re-render.
        Select(driver.find_element(By.ID, "room-status-select")).select_by_value("active")
        wait(driver, _room_status_contains("active"))


# ── Human message styling ────────────────────────────────────────────────


class TestMessageStyling:
    def test_human_message_has_class(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "msg-input")))
        unique = f"style-test-{int(time.time() * 1000)}"
        driver.find_element(By.ID, "msg-input").send_keys(unique)
        driver.find_element(By.ID, "msg-input").send_keys(Keys.RETURN)
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), unique))
        assert len(driver.find_elements(By.CSS_SELECTOR, "#messages .msg.human")) > 0


# ── XSS prevention ──────────────────────────────────────────────────────


class TestXSSPrevention:
    def test_xss_in_agent_name_escaped(self, driver, server_url, logged_in, test_data):
        xss_name = '<img src=x onerror="alert(1)">'
        # Use existing agent token to create room via API token, or use driver's session
        room_name = f"xss-{int(time.time())}"
        # Create room via driver's existing session (avoids extra _login_session call)
        driver.execute_script(f"""
            window._xss_token=null;
            fetch('/ui/api/rooms', {{method:'POST', headers:{{'Content-Type':'application/json'}},
                body: JSON.stringify({{name: '{room_name}', topic: ''}})
            }}).then(r=>r.json()).then(d=>window._xss_token=d.join_token)
        """)
        wait(driver, lambda d: d.execute_script("return window._xss_token") is not None)
        join_token = driver.execute_script("return window._xss_token")
        _api_post(
            server_url,
            "/api/v1/join",
            {
                "join_token": join_token,
                "name": xss_name,
                "role": "agent",
            },
        )
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if room_name in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".agent-card")))
        # Atomic read: .agent-card is re-rendered by the SSE stream, so read all
        # innerHTML in one call to avoid stale-element on the captured handles.
        card_html = driver.execute_script(
            "return Array.from(document.querySelectorAll('.agent-card')).map(c => c.innerHTML).join('')"
        )
        assert "<img" not in card_html.lower(), f"XSS not escaped: {card_html[:200]}"

    def test_xss_in_message_escaped(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "msg-input")))
        driver.find_element(By.ID, "msg-input").send_keys('<script>document.title="PWNED"</script>')
        driver.find_element(By.ID, "msg-input").send_keys(Keys.RETURN)
        # The payload renders as escaped text containing PWNED; wait for it to land.
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), "PWNED"))
        assert driver.title != "PWNED"
        assert "<script>" not in driver.find_element(By.ID, "messages").get_attribute("innerHTML")

    def test_markdown_renders_via_sri_pinned_libs(self, driver, server_url, logged_in, test_data):
        # Proves marked + DOMPurify (now SRI-pinned) actually loaded: renderMd
        # falls back to plain-escape on load failure, so a wrong SRI hash would
        # emit literal "**bold**"/"*em*" text with NO <strong>/<em> tags here.
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "msg-input")))
        marker = f"md{int(time.time() * 1000)}"
        driver.find_element(By.ID, "msg-input").send_keys(f"**{marker}** and *emph*")
        driver.find_element(By.ID, "msg-input").send_keys(Keys.RETURN)
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), marker))
        html = driver.find_element(By.ID, "messages").get_attribute("innerHTML")
        assert f"<strong>{marker}</strong>" in html, f"marked/DOMPurify did not render markdown: {html[-300:]}"
        assert "<em>emph</em>" in html


# ── Logout ───────────────────────────────────────────────────────────────


# ── DM conversation view ────────────────────────────────────────────────


class TestDMConversation:
    def test_dm_send_and_history(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".dm-btn")))
        driver.find_element(By.CSS_SELECTOR, ".dm-btn").click()
        WebDriverWait(driver, 5).until(
            lambda d: d.find_element(By.ID, "dm-modal").value_of_css_property("display") != "none"
        )
        unique = f"dm-{int(time.time())}"
        driver.find_element(By.ID, "dm-input").send_keys(unique)
        driver.find_element(By.XPATH, "//button[contains(text(),'Send DM')]").click()
        # Wait for the DM to appear in history rather than sleeping.
        wait(driver, EC.text_to_be_present_in_element((By.ID, "dm-messages"), unique))
        # Modal stays open
        assert driver.find_element(By.ID, "dm-modal").value_of_css_property("display") != "none"
        # Message in history
        assert unique in driver.find_element(By.ID, "dm-messages").text
        driver.find_element(By.XPATH, "//div[@id='dm-modal']//button[contains(text(),'Close')]").click()


# ── Room DMs (god-mode visibility of agent-to-agent DMs) ─────────────────


@pytest.fixture(scope="session")
def room_dms_data(server_url):
    """Create a room with two agents and an agent-to-agent DM for Room DMs tests."""
    room_name = f"room-dms-{int(time.time())}"
    opener = _login_session(server_url)
    room = _ui_api_post(opener, server_url, "/ui/api/rooms", {"name": room_name, "topic": "DM visibility test"})
    alice = _api_post(
        server_url,
        "/api/v1/join",
        {"join_token": room["join_token"], "name": "Alice", "role": "dev"},
    )
    bob = _api_post(
        server_url,
        "/api/v1/join",
        {"join_token": room["join_token"], "name": "Bob", "role": "dev"},
    )
    # Alice DMs Bob (agent-to-agent, invisible to humans before this feature)
    _api_post(
        server_url,
        f"/api/v1/dm/{bob['id']}",
        {"text": "secret side-channel msg"},
        token=alice["agent_token"],
    )
    return {"room": room_name, "alice": alice, "bob": bob}


class TestRoomDMs:
    def _select_room(self, driver, server_url, room_dms_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if room_dms_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "room-title")))

    def test_room_dms_button_exists(self, driver, server_url, logged_in, room_dms_data):
        self._select_room(driver, server_url, room_dms_data)
        # Atomic read: the button set includes .dm-btn inside .agent-card, which
        # the SSE stream re-renders — read all button text in one call.
        btn_texts = driver.execute_script(
            "return Array.from(document.querySelectorAll('button')).map(b => b.innerText.trim())"
        )
        btns = [t for t in btn_texts if t == "Room DMs"]
        assert len(btns) == 1

    def test_room_dms_modal_opens_with_room_name(self, driver, server_url, logged_in, room_dms_data):
        self._select_room(driver, server_url, room_dms_data)
        driver.find_element(By.XPATH, "//button[contains(text(),'Room DMs')]").click()
        WebDriverWait(driver, 5).until(
            lambda d: d.find_element(By.ID, "room-dms-modal").value_of_css_property("display") != "none"
        )
        title = driver.find_element(By.ID, "room-dms-room-name").text
        assert room_dms_data["room"] in title
        # Close it
        driver.find_element(By.XPATH, "//div[@id='room-dms-modal']//button[contains(text(),'Close')]").click()

    def test_room_dms_shows_agent_to_agent_dm(self, driver, server_url, logged_in, room_dms_data):
        self._select_room(driver, server_url, room_dms_data)
        driver.find_element(By.XPATH, "//button[contains(text(),'Room DMs')]").click()
        WebDriverWait(driver, 5).until(
            lambda d: d.find_element(By.ID, "room-dms-modal").value_of_css_property("display") != "none"
        )
        # Wait for content to load (polls every 3s, but first load is immediate)
        WebDriverWait(driver, 5).until(
            lambda d: "secret side-channel msg" in d.find_element(By.ID, "room-dms-messages").text
        )
        content = driver.find_element(By.ID, "room-dms-messages").text
        assert "Alice" in content
        assert "Bob" in content
        driver.find_element(By.XPATH, "//div[@id='room-dms-modal']//button[contains(text(),'Close')]").click()

    def test_room_dms_modal_close_button(self, driver, server_url, logged_in, room_dms_data):
        self._select_room(driver, server_url, room_dms_data)
        driver.find_element(By.XPATH, "//button[contains(text(),'Room DMs')]").click()
        WebDriverWait(driver, 5).until(
            lambda d: d.find_element(By.ID, "room-dms-modal").value_of_css_property("display") != "none"
        )
        driver.find_element(By.XPATH, "//div[@id='room-dms-modal']//button[contains(text(),'Close')]").click()
        wait(driver, lambda d: d.find_element(By.ID, "room-dms-modal").value_of_css_property("display") == "none")
        assert driver.find_element(By.ID, "room-dms-modal").value_of_css_property("display") == "none"

    def test_room_dms_escape_dismisses_modal(self, driver, server_url, logged_in, room_dms_data):
        self._select_room(driver, server_url, room_dms_data)
        driver.find_element(By.XPATH, "//button[contains(text(),'Room DMs')]").click()
        WebDriverWait(driver, 5).until(
            lambda d: d.find_element(By.ID, "room-dms-modal").value_of_css_property("display") != "none"
        )
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        wait(driver, lambda d: d.find_element(By.ID, "room-dms-modal").value_of_css_property("display") == "none")
        assert driver.find_element(By.ID, "room-dms-modal").value_of_css_property("display") == "none"


# ── Room persistence ─────────────────────────────────────────────────────


class TestInfoPanel:
    def test_panel_closes_via_button(self, mobile_driver, server_url, mobile_logged_in, test_data):
        _mobile_select_room(mobile_driver, server_url, test_data)
        mobile_driver.find_element(By.CSS_SELECTOR, ".panel-toggle").click()
        panel = mobile_driver.find_element(By.ID, "right-panel")
        wait(mobile_driver, lambda d: panel.is_displayed())
        assert panel.is_displayed()
        mobile_driver.find_element(By.CSS_SELECTOR, ".panel-close").click()
        wait(mobile_driver, lambda d: not panel.is_displayed())
        assert not panel.is_displayed()

    def test_panel_closes_via_overlay(self, mobile_driver, server_url, mobile_logged_in, test_data):
        _mobile_select_room(mobile_driver, server_url, test_data)
        mobile_driver.find_element(By.CSS_SELECTOR, ".panel-toggle").click()
        wait(mobile_driver, lambda d: d.find_element(By.ID, "right-panel").is_displayed())
        assert mobile_driver.find_element(By.ID, "right-panel").is_displayed()
        # Click the overlay on the left side (not covered by the panel)
        overlay = mobile_driver.find_element(By.ID, "panel-overlay")
        mobile_driver.execute_script("arguments[0].click()", overlay)
        wait(mobile_driver, lambda d: not d.find_element(By.ID, "right-panel").is_displayed())
        assert not mobile_driver.find_element(By.ID, "right-panel").is_displayed()


class TestBrowserNavigation:
    def test_back_returns_to_sidebar(self, mobile_driver, server_url, mobile_logged_in, test_data):
        """Browser back from room view should return to sidebar, not leave the app."""
        mobile_driver.get(server_url + "#")
        WebDriverWait(mobile_driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        # Tap room (pushes one history entry)
        for item in mobile_driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(mobile_driver, 5).until(EC.visibility_of_element_located((By.ID, "room-view")))
        assert mobile_driver.find_element(By.ID, "main").is_displayed()
        mobile_driver.back()
        WebDriverWait(mobile_driver, 5).until(EC.visibility_of_element_located((By.ID, "sidebar")))
        assert "/login" not in mobile_driver.current_url

    def test_back_from_second_room(self, driver, server_url, logged_in, test_data):
        """Open room A, open room B, back should return to room A."""
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        items = driver.find_elements(By.CSS_SELECTOR, ".room-item")
        # Click first room
        items[0].click()
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, "room-title")))
        first_title = driver.find_element(By.ID, "room-title").text
        # Click second room (if exists)
        if len(items) < 2:
            return  # skip if only one room
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        items = driver.find_elements(By.CSS_SELECTOR, ".room-item")
        items[1].click()
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, "room-title")))
        second_title = driver.find_element(By.ID, "room-title").text
        assert first_title != second_title
        driver.back()
        wait(driver, lambda d: d.find_element(By.ID, "room-title").text == first_title)
        assert driver.find_element(By.ID, "room-title").text == first_title


class TestRoomPersistence:
    def test_room_persists_across_refresh(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if test_data["room"] in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, "room-title")))
        title_before = driver.find_element(By.ID, "room-title").text
        driver.refresh()
        WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "room-title")))
        assert driver.find_element(By.ID, "room-title").text == title_before


# ── Empty states ─────────────────────────────────────────────────────────


class TestEmptyStates:
    def test_empty_room_shows_placeholder(self, driver, server_url, logged_in):
        room_name = f"empty-{int(time.time() * 1000)}"
        driver.execute_script(f"""
            window._empty_done=false;
            fetch('/ui/api/rooms', {{method:'POST', headers:{{'Content-Type':'application/json'}},
                body: JSON.stringify({{name: '{room_name}', topic: ''}})
            }}).then(()=>window._empty_done=true)
        """)
        # Wait for the create request to finish before reloading (was time.sleep).
        wait(driver, lambda d: d.execute_script("return window._empty_done"))
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        # Wait for our freshly-created room to render, then click it.
        wait(
            driver,
            lambda d: any(
                room_name in t
                for t in d.execute_script(
                    "return Array.from(document.querySelectorAll('.room-item')).map(i => i.innerText)"
                )
            ),
        )
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if room_name in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, "messages")))
        wait(driver, EC.text_to_be_present_in_element((By.ID, "messages"), "No messages yet"))
        assert "No messages yet" in driver.find_element(By.ID, "messages").text


# ── Logout ───────────────────────────────────────────────────────────────


class TestLogout:
    def test_logout_redirects_to_login(self, fresh_driver, server_url):
        # Order-independent: use a function-scoped browser with its own login so
        # logging out here does NOT invalidate the shared session-scoped `driver`
        # other tests rely on. This removes the old "run last" (TestZLogout) hack.
        _do_login(fresh_driver, server_url)
        fresh_driver.get(server_url)
        wait(fresh_driver, EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        fresh_driver.find_element(By.XPATH, "//button[contains(text(),'Logout')]").click()
        wait(fresh_driver, EC.url_contains("/login"))
        assert "/login" in fresh_driver.current_url
