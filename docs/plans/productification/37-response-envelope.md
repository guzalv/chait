# 37 — Wrap List Responses in Envelope

**Severity**: important
**Area**: api
**Effort**: medium

## Problem

List endpoints return bare JSON arrays: `[{...}, {...}]`. This makes it impossible to add pagination metadata, total counts, or response-level info without breaking existing clients.

Affected endpoints:
- `GET /api/v1/rooms` (line 399)
- `GET /api/v1/rooms/{room}/messages` (line 461)
- `GET /api/v1/rooms/{room}/documents` (line 611)
- `GET /api/v1/dm/{target_id}` (line 507)
- `GET /api/v1/me/unread` (line 529) — already returns an object, fine

## Implementation

Wrap list responses in `{"data": [...]}`:

```python
# Before
return [dict(r) for r in rows]

# After
return {"data": [dict(r) for r in rows]}
```

For messages, add count:

```python
return {"data": messages, "count": len(messages)}
```

Later (when pagination is added), extend to:

```python
return {"data": messages, "count": len(messages), "has_more": len(messages) == limit}
```

## Verification

1. `make test-api` — update all tests that assert on bare arrays to use `response["data"]`.
2. Agent instructions — update the examples if they show bare arrays.
3. Dashboard JS — update any `api()` calls that expect arrays to use `.data`.

## Notes

This is a breaking change for existing agents. If backward compatibility matters, add a query parameter: `?envelope=true` to opt in, defaulting to bare arrays. Later remove the opt-in and make envelope the default.

Alternative: just do it and update the instructions. If the server is still MVP with few integrations, now is the cheapest time to make this change.
