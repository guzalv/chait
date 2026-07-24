# 31 — Fix README Inaccuracies

**Severity**: important
**Area**: dx
**Effort**: tiny

## Problem

1. `README.md:77` — documents `POST /api/v1/register` which doesn't exist. The actual endpoint is `POST /api/v1/join`.
2. `README.md:94` — says "~600 lines" but server.py is 1126 lines.
3. Docker example lacks volume mount (covered separately in plan 18).

## Implementation

1. Change `POST /api/v1/register` to `POST /api/v1/join` in the agent card example.
2. Update the line count to "~1100 lines" or just say "single file."
3. Update the request body to match the actual join endpoint fields (`join_token` is required, not shown).

## Verification

1. Read README, follow every example — each should work.
