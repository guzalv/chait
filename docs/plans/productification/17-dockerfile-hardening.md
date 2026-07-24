# 17 — Dockerfile Hardening

**Severity**: critical (non-root), important (rest)
**Area**: ops, security
**Effort**: small

## Problem

1. No `USER` directive — container runs as root. Container escape gives host root access.
2. No `.dockerignore` — `.venv/`, `.git/`, `data/`, `tests/` all copied into build context, slowing builds.
3. No `HEALTHCHECK` — orchestrators can't detect hung processes.
4. No `VOLUME /data` — `docker run` without `-v` silently loses all data.

## Implementation

### Step 1: Create `.dockerignore`

Create file `.dockerignore`:

```
.venv/
.git/
.github/
__pycache__/
.pytest_cache/
data/
tests/
docs/
*.log
*.md
Makefile
launch.sh
```

### Step 2: Update Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app

RUN useradd -r -s /bin/false chait && mkdir -p /data && chown chait:chait /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY templates/ templates/

ENV CHAIT_DATA_DIR=/data CHAIT_PORT=3100
EXPOSE 3100
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3100/health')" || exit 1

USER chait
CMD ["python", "server.py"]
```

Note: the `COPY templates/ templates/` line depends on plan 14 (extract HTML templates). If that hasn't been done yet, omit it.

The HEALTHCHECK depends on plan 16 (health endpoint). If that hasn't been done yet, use `/api/v1/instructions` as a temporary probe target.

## Verification

1. `docker build -t chait .` — builds successfully.
2. `docker run -p 3100:3100 -v chait-data:/data chait` — starts, reachable, runs as non-root.
3. `docker exec <container> whoami` — should return `chait`, not `root`.
4. `docker inspect <container> | grep Health` — shows health status.
