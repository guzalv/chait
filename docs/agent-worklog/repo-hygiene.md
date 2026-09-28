# Repo hygiene (share-readiness)

## Changes
- Added an MIT `LICENSE` (© 2026 Guzman) and a `## License` section in the README. Without a license, the default "all rights reserved" applies and colleagues legally cannot use/modify/redistribute the project — this blocked the stated goal of sharing it.
- Removed the stale hard-coded test counts from the README (`76/39/13/24`); they were wrong (actual ~63/17/54) and drift on every PR. Replaced with descriptive text so they no longer rot.
- Added `selenium` to `make test-deps` so `make test-ui` works from a clean checkout (previously selenium was assumed to be pre-installed in the venv and was not declared anywhere).

## Decisions
- MIT chosen per owner: permissive, shortest, most common for small tools.
- Dropped exact test counts rather than update them, since they change with almost every PR and provide little value versus the maintenance cost.

## Testing
- Docs/build-config only; no runtime code changed. `make test-deps` now additionally installs selenium.
