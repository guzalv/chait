# 05 — Remove Unused Dependencies

**Severity**: important
**Area**: architecture
**Effort**: tiny

## Problem

`requirements.txt` includes three packages that are never imported anywhere in server.py:
- `websockets>=12.0` — no WebSocket endpoints exist
- `jinja2>=3.1.0` — HTML is raw Python strings, no template rendering
- `bcrypt>=4.0.0` — password comparison is plaintext string equality

These add attack surface, install time, image size, and mislead anyone reading requirements.txt into thinking template rendering and password hashing are in use.

## Implementation

Edit `requirements.txt` to remove the three lines, leaving:

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
aiosqlite>=0.20.0
```

## Verification

1. `rm -rf .venv && make deps` — clean install succeeds.
2. `make test-api && make test-integration` — all tests pass.
3. `python -c "import server"` — no import errors.
