# 14 — Extract HTML Templates from server.py

**Severity**: important
**Area**: architecture
**Effort**: medium

## Problem

`server.py:666-934` — 269 lines of HTML/CSS/JS are embedded as a Python string constant (`DASHBOARD_HTML`). The login page HTML is also inline (around line 642-646). This means:
- No syntax highlighting for HTML/CSS/JS in editors
- Can't lint the JavaScript
- Every edit requires awareness of Python string escaping
- Bloats server.py making it harder to navigate

## Implementation

### Step 1: Create template files

```
mkdir -p templates
```

Extract `DASHBOARD_HTML` (lines 666-934) to `templates/dashboard.html`.
Extract login page HTML (around line 642-646) to `templates/login.html`.

### Step 2: Load at startup

At the top of server.py, add:

```python
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"
```

Replace the `DASHBOARD_HTML = """..."""` block with:

```python
DASHBOARD_HTML = (_TEMPLATE_DIR / "dashboard.html").read_text()
```

For the login page, similarly:

```python
LOGIN_HTML = (_TEMPLATE_DIR / "login.html").read_text()
```

Then use `LOGIN_HTML` in the `login_page` function.

### Step 3: Update Dockerfile

Add `COPY templates/ templates/` after `COPY server.py .` in the Dockerfile.

### Step 4: Update .gitignore if needed

Templates should be tracked in git (they're source code, not generated).

## Verification

1. `make test-api && make test-integration` — pass.
2. `make test-ui` — UI tests pass (dashboard renders correctly).
3. `docker build -t chait .` — builds successfully.
4. Manual: open dashboard, verify it looks identical to before.

## Notes

Do NOT introduce a template engine (Jinja2, etc.) for this. Raw `read_text()` is sufficient. The HTML already uses JavaScript for dynamic content, not server-side templating.
