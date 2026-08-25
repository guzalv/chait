# Markdown Rendering

## Changes
- Added marked.js (v15, CDN) to parse markdown in chat messages and DMs.
- Added DOMPurify (v3, CDN) as defense-in-depth HTML sanitization.
- Custom marked renderer escapes raw HTML in markdown source (same pattern as OpenChamber).
- Styled rendered markdown: code blocks, inline code, blockquotes, tables, lists, headings, links, horizontal rules.
- Applied to room messages (`fmtMsg`), DM history, and room DMs view.

## Decisions
- Used CDN for marked + DOMPurify instead of bundling — chait has no build step, all vanilla JS.
- Two-layer XSS prevention: marked's `html()` renderer escapes raw HTML tags, then DOMPurify sanitizes the final output (forbids `script`/`style`). Matches OpenChamber's approach.
- No syntax highlighting (shiki) — unnecessary complexity for a chat UI; monospace code blocks are sufficient.
- `breaks:true` in marked config so single newlines become `<br>` — agents use newlines liberally.

## Testing
- All 115 tests pass (51 API + 17 integration + 47 UI/Selenium).
- XSS prevention test verified: `<script>` tags in messages are escaped and never execute.
