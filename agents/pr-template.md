# PR Description Template

No rigid template — match this skeleton, drop sections that don't apply.

## Structure

1. **Intro** — no heading, 1-3 sentences on the problem/gap being fixed. Use `## Problem` + `## Solution` headings instead if it needs more than a couple sentences.
2. **## Changes** (or `## What`) — bullet list of what changed and why.
3. **## Decisions** (optional) — non-obvious choices and rejected alternatives. Omit if there's nothing to explain.
4. **## Testing** — always include. State test counts (`make test` output) and any manual verification performed.
5. **## Out of scope** (optional) — things noticed but deliberately not addressed here.

## Rules

- One paragraph per line — no hard-wrapping at a fixed column width. Let GitHub wrap it in the rendered view.
- If a worklog entry exists for this change, reference it: "Full rationale in `docs/agent-worklog/<topic>.md`."
- Always create as draft (`gh pr create --draft`); mark ready only when asked (see `AGENTS.md`).
