# 03 — Upload Size Limit and Filename Sanitization

**Severity**: critical
**Area**: security, performance
**Effort**: small

## Problem

1. `server.py:598` and `server.py:1061` — `await file.read()` reads entire upload into memory with no size limit. A single large upload can OOM the server.
2. `server.py:599` and `server.py:1062` — `file.filename` is used directly in the path. Could contain `../` for path traversal.

## Implementation

### Size limit

Add a constant near the top of server.py (after the other constants around line 35):

```python
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
```

In both upload endpoints (agent at ~line 598, UI at ~line 1061), replace:

```python
content = await file.read()
```

with:

```python
content = await file.read(MAX_UPLOAD_BYTES + 1)
if len(content) > MAX_UPLOAD_BYTES:
    raise HTTPException(413, "File too large (50 MB max)")
```

### Filename sanitization

In both upload endpoints (agent at ~line 599, UI at ~line 1062), replace:

```python
(room_doc_dir / f"{doc_id}_{file.filename}").write_bytes(content)
```

with:

```python
safe_name = Path(file.filename).name or "unnamed"
(room_doc_dir / f"{doc_id}_{safe_name}").write_bytes(content)
```

Also update the DB insert to store the sanitized name (same lines, the `filename` field in the INSERT).

## Verification

1. `make test-api` — existing tests pass.
2. New test: upload a file >50MB, assert 413 response.
3. New test: upload with filename `../../etc/passwd`, verify file is saved as `{id}_passwd` in the correct directory.
