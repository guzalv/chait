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
    token = agent["token"]

    # 4. Post messages using agent token
    _api_post(server_url, f"/api/v1/rooms/{room_name}/messages", {"text": "Hello from TestBot"}, token=token)
    _api_post(server_url, f"/api/v1/rooms/{room_name}/messages", {"text": "Second message"}, token=token)

    return {"room": room_name, "agent": agent, "token": token}


@pytest.fixture(scope="session")
def driver():
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
        "--window-size=1920,1080",
        f"--user-data-dir={tempfile.mkdtemp(dir='/tmp')}",
    ]:
        opts.add_argument(a)
    snap_driver = "/snap/bin/chromium.chromedriver"
    if os.path.exists(snap_driver):
        svc = webdriver.ChromeService(executable_path=snap_driver)
        d = webdriver.Chrome(service=svc, options=opts)
    else:
        d = webdriver.Chrome(options=opts)
    d.implicitly_wait(2)
    yield d
    d.quit()


@pytest.fixture(scope="session")
def logged_in(driver, server_url):
    """Log in once for all tests that need a session."""
    driver.get(f"{server_url}/login")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
    driver.find_element(By.CSS_SELECTOR, 'input[name="user"]').send_keys(USER)
    driver.find_element(By.CSS_SELECTOR, 'input[name="password"]').send_keys(PASS)
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    WebDriverWait(driver, 10).until(lambda d: "/login" not in d.current_url)
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
        time.sleep(1)
        assert "Invalid" in driver.page_source or "invalid" in driver.page_source.lower()

    def test_dashboard_redirects_without_session(self, driver, server_url):
        # Clear cookies to simulate no session
        driver.delete_all_cookies()
        driver.get(server_url)
        time.sleep(1)
        assert "/login" in driver.current_url


# ── Dashboard layout ─────────────────────────────────────────────────────


class TestDashboard:
    def test_sidebar_shows_rooms(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        items = driver.find_elements(By.CSS_SELECTOR, ".room-item")
        names = [i.text.split("\n")[0] for i in items]
        assert test_data["room"] in " ".join(names)

    def test_rooms_have_status_badges(self, driver, server_url, logged_in, test_data):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-status")))
        badges = driver.find_elements(By.CSS_SELECTOR, ".room-status")
        assert len(badges) > 0
        classes = " ".join(b.get_attribute("class") for b in badges)
        assert "status-active" in classes

    def test_right_panel_sections(self, driver, server_url, logged_in):
        driver.get(server_url)
        panel = driver.find_element(By.ID, "right-panel")
        panel_text = panel.text.lower()
        assert "room members" in panel_text
        assert "documents" in panel_text

    def test_select_room_placeholder(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "no-room")))
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
        card_text = cards[0].text
        assert "TestBot" in card_text
        assert "tester" in card_text

    def test_agent_card_shows_skills(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        cards = driver.find_elements(By.CSS_SELECTOR, ".agent-card")
        skill_text = " ".join(c.text for c in cards)
        assert "selenium" in skill_text or "pytest" in skill_text

    def test_agent_card_has_dm_button(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        dm_btns = driver.find_elements(By.CSS_SELECTOR, ".agent-card .dm-btn")
        assert len(dm_btns) >= 1


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
        time.sleep(1)
        msgs = driver.find_elements(By.CSS_SELECTOR, "#messages .msg")
        assert any("Button send test" in m.text for m in msgs)

    def test_send_via_enter(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        textarea = driver.find_element(By.ID, "msg-input")
        textarea.send_keys("Enter send test")
        textarea.send_keys(Keys.RETURN)
        time.sleep(1)
        msgs = driver.find_elements(By.CSS_SELECTOR, "#messages .msg")
        assert any("Enter send test" in m.text for m in msgs)

    def test_sent_message_has_human_author_and_priority(self, driver, server_url, logged_in, test_data):
        self._select_room(driver, server_url, test_data)
        # Look for any human message already sent
        time.sleep(1)
        msgs = driver.find_elements(By.CSS_SELECTOR, "#messages .msg")
        human_msgs = [m for m in msgs if "Human" in m.text and "[god]" in m.text]
        assert len(human_msgs) > 0
        # At least one should have priority badge
        priority_msgs = [m for m in msgs if "PRIORITY" in m.text]
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
        fi = driver.find_element(By.ID, "file-input")
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
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".dm-btn")))
        driver.find_element(By.CSS_SELECTOR, ".dm-btn").click()
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
        time.sleep(0.5)
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
        time.sleep(2)
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
        _api_post(server_url, f"/api/v1/rooms/{test_data['room']}/messages", {"text": unique}, token=test_data["token"])
        # Wait for poll interval (3s in JS)
        time.sleep(5)
        assert unique in driver.find_element(By.ID, "messages").text


# ── Mobile viewport tests ────────────────────────────────────────────────


def _make_chrome(mobile=False):
    """Create a Chrome driver, optionally with mobile emulation."""
    opts = webdriver.ChromeOptions()
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
        opts.add_experimental_option("mobileEmulation", {
            "deviceMetrics": {"width": 390, "height": 844, "pixelRatio": 3.0},
            "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        })
    else:
        opts.add_argument("--window-size=1920,1080")
    snap_driver = "/snap/bin/chromium.chromedriver"
    if os.path.exists(snap_driver):
        svc = webdriver.ChromeService(executable_path=snap_driver)
        return webdriver.Chrome(service=svc, options=opts)
    return webdriver.Chrome(options=opts)


@pytest.fixture(scope="session")
def mobile_driver():
    d = _make_chrome(mobile=True)
    d.implicitly_wait(2)
    yield d
    d.quit()


@pytest.fixture(scope="session")
def mobile_logged_in(mobile_driver, server_url):
    mobile_driver.get(f"{server_url}/login")
    WebDriverWait(mobile_driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
    mobile_driver.find_element(By.CSS_SELECTOR, 'input[name="user"]').send_keys(USER)
    mobile_driver.find_element(By.CSS_SELECTOR, 'input[name="password"]').send_keys(PASS)
    mobile_driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    WebDriverWait(mobile_driver, 10).until(lambda d: "/login" not in d.current_url)
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
        mobile_driver.get(server_url)
        WebDriverWait(mobile_driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        assert mobile_driver.find_element(By.ID, "sidebar").is_displayed()

    def test_mobile_onclick_not_broken(self, mobile_driver, server_url, mobile_logged_in):
        """Verify room onclick attributes contain valid JS (no quote truncation)."""
        mobile_driver.get(server_url)
        WebDriverWait(mobile_driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in mobile_driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            onclick = item.get_attribute("onclick")
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
        time.sleep(0.5)
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
        time.sleep(0.3)
        modal = driver.find_element(By.ID, "new-room-modal")
        assert modal.value_of_css_property("display") != "none"
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.3)
        assert modal.value_of_css_property("display") == "none"

    def test_click_outside_closes_modal(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        driver.find_element(By.XPATH, "//button[contains(text(),'+ Room')]").click()
        time.sleep(0.3)
        modal = driver.find_element(By.ID, "new-room-modal")
        assert modal.value_of_css_property("display") != "none"
        driver.execute_script("document.getElementById('new-room-modal').click()")
        time.sleep(0.3)
        assert modal.value_of_css_property("display") == "none"


# ── Room creation flow ───────────────────────────────────────────────────


class TestRoomCreation:
    def test_create_room_shows_token(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        driver.find_element(By.XPATH, "//button[contains(text(),'+ Room')]").click()
        time.sleep(0.3)
        name_input = driver.find_element(By.ID, "new-room-name")
        room_name = f"ui-create-{int(time.time())}"
        name_input.send_keys(room_name)
        driver.find_element(By.ID, "create-room-btn").click()
        WebDriverWait(driver, 5).until(
            lambda d: d.find_element(By.ID, "new-room-token").text.startswith("chait-")
        )
        assert driver.find_element(By.ID, "new-room-token").text.startswith("chait-")

    def test_created_room_in_sidebar(self, driver, server_url, logged_in):
        for btn in driver.find_elements(By.CSS_SELECTOR, "#new-room-modal .btn"):
            if "Close" in btn.text:
                btn.click()
                break
        time.sleep(0.5)
        items = driver.find_elements(By.CSS_SELECTOR, ".room-item")
        assert any("ui-create-" in i.text for i in items)


# ── Connection status ────────────────────────────────────────────────────


class TestConnectionStatus:
    def test_live_indicator_visible(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "conn-status")))
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
        time.sleep(2)
        items = driver.find_elements(By.CSS_SELECTOR, ".room-item")
        assert any("waiting" in i.text.lower() for i in items if test_data["room"] in i.text)
        # Reset
        Select(driver.find_element(By.ID, "room-status-select")).select_by_value("active")
        time.sleep(1)


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
        driver.find_element(By.ID, "msg-input").send_keys("style-test")
        driver.find_element(By.ID, "msg-input").send_keys(Keys.RETURN)
        time.sleep(1)
        assert len(driver.find_elements(By.CSS_SELECTOR, "#messages .msg.human")) > 0


# ── XSS prevention ──────────────────────────────────────────────────────


class TestXSSPrevention:
    def test_xss_in_agent_name_escaped(self, driver, server_url, logged_in, test_data):
        xss_name = '<img src=x onerror="alert(1)">'
        # Use existing agent token to create room via API token, or use driver's session
        room_name = f"xss-{int(time.time())}"
        # Create room via driver's existing session (avoids extra _login_session call)
        driver.execute_script(f"""
            fetch('/ui/api/rooms', {{method:'POST', headers:{{'Content-Type':'application/json'}},
                body: JSON.stringify({{name: '{room_name}', topic: ''}})
            }}).then(r=>r.json()).then(d=>window._xss_token=d.join_token)
        """)
        time.sleep(1)
        join_token = driver.execute_script("return window._xss_token")
        _api_post(server_url, "/api/v1/join", {
            "join_token": join_token, "name": xss_name, "role": "agent",
        })
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if room_name in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".agent-card")))
        card_html = "".join(c.get_attribute("innerHTML") for c in driver.find_elements(By.CSS_SELECTOR, ".agent-card"))
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
        time.sleep(1)
        assert driver.title != "PWNED"
        assert "<script>" not in driver.find_element(By.ID, "messages").get_attribute("innerHTML")


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
        time.sleep(1)
        # Modal stays open
        assert driver.find_element(By.ID, "dm-modal").value_of_css_property("display") != "none"
        # Message in history
        assert unique in driver.find_element(By.ID, "dm-messages").text
        driver.find_element(By.XPATH, "//div[@id='dm-modal']//button[contains(text(),'Close')]").click()


# ── Room persistence ─────────────────────────────────────────────────────


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
        room_name = f"empty-{int(time.time())}"
        driver.execute_script(f"""
            fetch('/ui/api/rooms', {{method:'POST', headers:{{'Content-Type':'application/json'}},
                body: JSON.stringify({{name: '{room_name}', topic: ''}})
            }})
        """)
        time.sleep(1)
        driver.get(server_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".room-item")))
        for item in driver.find_elements(By.CSS_SELECTOR, ".room-item"):
            if room_name in item.text:
                item.click()
                break
        WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, "messages")))
        time.sleep(1)
        assert "No messages yet" in driver.find_element(By.ID, "messages").text


# ── Logout (MUST be last — invalidates session) ─────────────────────────


class TestZLogout:
    """Named with Z prefix to run last — logout invalidates the shared session."""

    def test_logout_redirects_to_login(self, driver, server_url, logged_in):
        driver.get(server_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#sidebar h1")))
        driver.find_element(By.XPATH, "//button[contains(text(),'Logout')]").click()
        WebDriverWait(driver, 5).until(lambda d: "/login" in d.current_url)
        assert "/login" in driver.current_url
