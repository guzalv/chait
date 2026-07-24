# 10 — Fix Stored XSS in Dashboard

**Severity**: critical
**Area**: security
**Effort**: small

## Problem

The dashboard JS renders dynamic data via innerHTML without consistent escaping:

1. `server.py:849` — `${m.author_name}` not escaped in message name span
2. `server.py:815-818` — room names in sidebar rendered without escaping, including in `onclick` attribute
3. `server.py:857-862` — agent names in member cards not escaped
4. `server.py:851` — `esc()` function only escapes `& < >`, missing `"` and `'` (breaks attribute contexts)

An agent joining with name `<img src=x onerror="...">` achieves arbitrary JS execution in every human's browser.

## Implementation

### Step 1: Fix `esc()` to handle all contexts

At `server.py:851`, replace:

```javascript
// Before
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

// After
function esc(s){if(s==null)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
```

### Step 2: Apply `esc()` everywhere dynamic data is rendered

In `fmtMsg()` (line 849):
```javascript
// Before
<span class="name">${m.author_name}</span> <span class="role">[${m.author_role}]</span>

// After
<span class="name">${esc(m.author_name)}</span> <span class="role">[${esc(m.author_role)}]</span>
```

In `loadRooms()` (lines 815-818), escape room names in both display text and onclick handlers:
```javascript
// Before
onclick="selectRoom('${r.name}')"
<span>${r.name}</span>

// After
onclick="selectRoom(${JSON.stringify(r.name)})"
<span>${esc(r.name)}</span>
```

Use `JSON.stringify()` for attribute contexts (onclick, etc.) since it properly escapes quotes.

In member cards (lines 857-862):
```javascript
// Before
<div class="agent-name">${m.name}</div>

// After
<div class="agent-name">${esc(m.name)}</div>
```

In DM button (line 862):
```javascript
// Before
onclick="openDM('${m.id}','${esc(m.name)}')"

// After
onclick="openDM(${JSON.stringify(m.id)},${JSON.stringify(m.name)})"
```

In docs list (line 865):
```javascript
// Ensure filename is escaped
${esc(d.filename)}
```

In API token list (line 917):
```javascript
// Ensure token display is escaped
${esc(t.token)}
```

### Step 3: Audit all other innerHTML assignments

Search for all `innerHTML` assignments and verify every interpolated variable uses `esc()` or `JSON.stringify()`.

## Verification

1. `make test-ui` — existing UI tests pass.
2. Manual test: create an agent with name `<script>alert(1)</script>`, join a room, verify the name renders as escaped text in the dashboard (visible as literal `<script>` text, not executed).
3. Manual test: create a room with name `test'); alert(1);//`, verify no JS execution.
