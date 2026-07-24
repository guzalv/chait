# 40 — Non-blocking File I/O

**Severity**: important
**Area**: performance
**Effort**: small

## Problem

`server.py:599` and `server.py:1062` — `Path.write_bytes()` is a synchronous blocking syscall. On a slow disk or NFS mount, this blocks the entire asyncio event loop — all concurrent request handlers (including long-polls) freeze for the write duration.

Also `server.py:597, 1060` — `Path.mkdir()` is synchronous, though fast and rare.

## Implementation

Replace blocking file writes with `asyncio.to_thread`:

```python
# Before
(room_doc_dir / f"{doc_id}_{safe_name}").write_bytes(content)

# After
import asyncio
path = room_doc_dir / f"{doc_id}_{safe_name}"
await asyncio.to_thread(path.write_bytes, content)
```

For `mkdir`:

```python
# Before
room_doc_dir.mkdir(parents=True, exist_ok=True)

# After
await asyncio.to_thread(room_doc_dir.mkdir, parents=True, exist_ok=True)
```

If plan 03 (upload security) is implemented with streaming, the streaming write should also be in a thread:

```python
async def _write_upload(path: Path, file: UploadFile, max_size: int):
    def _do_write():
        size = 0
        with open(path, "wb") as f:
            while chunk := file.file.read(65536):
                size += len(chunk)
                if size > max_size:
                    os.unlink(path)
                    raise HTTPException(413, "File too large")
                f.write(chunk)
    await asyncio.to_thread(_do_write)
```

## Verification

1. `make test-api` — passes.
2. Upload a large file while agents are long-polling — long-poll responses should not be delayed.
