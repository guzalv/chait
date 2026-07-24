# 25 — Differentiate Human vs Agent Messages

**Severity**: critical
**Area**: ui
**Effort**: tiny

## Problem

Human "god mode" messages look nearly identical to agent messages. The only difference is a small red "PRIORITY" badge. In a busy room with 3+ agents, finding what the human said requires visual scanning.

## Implementation

### Step 1: Add CSS class for human messages

In the dashboard CSS:

```css
.msg.human { border-left: 3px solid #3b82f6; background: rgba(59,130,246,0.08); }
.msg.human .name { color: #60a5fa; }
```

### Step 2: Apply class in `fmtMsg()`

In the `fmtMsg()` function (around line 849):

```javascript
// Add role-based class
const isHuman = m.author_role === 'god';
const cls = `msg${pri}${isHuman ? ' human' : ''}`;
return `<div class="${cls}">...`;
```

## Verification

1. Manual: send a human message, verify it has a distinct blue left border and slightly tinted background.
2. Agent messages remain as-is.
3. `make test-ui` — passes.
