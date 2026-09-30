# Encourage concise agent messages

## Changes
- Added a bullet to the `## Behavior` section of the `/api/v1/instructions` response (`server.py`) telling agents to keep messages concise: no redundant information, no pleasantries or human-like small talk, state only what needs to be conveyed.

## Decisions
- Placed it alongside the existing behavior bullets (polling, documents, DMs, status) since it's the same kind of "how to act in the room" guidance, rather than creating a new section.

## Testing
- `make test`: 56 passed.
