# 27 — Escape Key Dismisses Modals

**Severity**: important
**Area**: ui
**Effort**: tiny

## Problem

Modals (new room, token, DM, API token) have no keyboard dismiss. On mobile, tapping outside does nothing. Users must find the small Close button.

## Implementation

Add to the dashboard JS:

```javascript
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal').forEach(m => m.style.display = 'none');
    }
});
```

Also add click-outside-to-dismiss on the overlay:

```javascript
document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', e => {
        if (e.target === modal) modal.style.display = 'none';
    });
});
```

The second handler works because `.modal` is the full-screen overlay div, and `.modal-content` is the inner box. Clicking the overlay (not the content) dismisses.

## Verification

1. Manual: open any modal, press Escape, verify it closes.
2. Manual: open any modal, click outside the modal content, verify it closes.
3. `make test-ui` — passes.
