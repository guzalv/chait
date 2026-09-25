# New Room Agent Prompt

## Changes
- New Room dialog now shows a ready-to-paste agent prompt below the join token. It references the `/api/v1/instructions` endpoint, the room name ("... to discuss about <room name>"), and the join token, so the user only needs to paste it into an agent.

## Decisions
- Built the prompt client-side from `location.origin`, the entered room name, and the returned `join_token` — no new endpoint or server change needed.
- Reused the existing `token-display` click-to-copy pattern.

## Testing
- Manual: create a room, verify the prompt renders with correct origin/name/token and copies on click.
