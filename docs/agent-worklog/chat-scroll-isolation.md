# Independent scrolling for the chat pane

## Changes
- Added `min-height:0` down the dashboard's nested flex chain (`#sidebar`, `#rooms-list`, `#main`, `#room-view`, `#messages`) and `flex-shrink:0` on `#room-header`, so `#messages` clips to its allotted height and its own `overflow-y:auto` actually engages instead of growing to fit content.

## Decisions
- Flex items default to `min-height:auto`, so nested flex columns (`#main` → `#room-view` → `#messages`, and `#sidebar` → `#rooms-list`) refused to shrink below their content's intrinsic height. With enough messages, the whole page grew taller than the viewport and `body` itself scrolled, dragging the room list and member panel out of view along with the chat — instead of just `#messages` scrolling internally as the CSS already intended.
- Also tried adding `overflow:hidden` to `body` as a belt-and-suspenders guard against any future overflow leaking to the page. Reverted it: it broke `TestInfoPanel::test_panel_closes_via_button`/`test_panel_closes_via_overlay`. Root cause: at the 390px mobile-emulation width used by those tests, the room header already overflows horizontally by ~40px (pre-existing, unrelated to this change — too many buttons for the width), pushing the "Info" toggle off-screen. The page being scrollable let WebDriver's native click scroll it into view before clicking; `overflow:hidden` removed that safety net. `min-height:0` alone fully fixes the reported issue, so the extra `overflow:hidden` wasn't needed.

## Testing
- Selenium check (seeded a room with 80 messages): before the fix, `body`/`main`/`room-view` grew to the full content height and the page scrolled while `#messages` never scrolled internally (`scrollHeight === clientHeight`). After the fix, only `#messages` scrolls (content height 3823px vs 527px viewport); `body`, `#sidebar`, and `#right-panel` stay pinned to the viewport height, and the sidebar header / room header / member panel stay at `top:0` regardless of chat scroll position.
- `make test`: 56 passed.
