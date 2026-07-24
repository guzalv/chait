# 44 — CI/CD Pipeline

**Severity**: critical
**Area**: ops, quality
**Effort**: medium

## Problem

The current CI (`ci.yml`) is minimal:
- Only runs `test-api` and `test-integration` (no lint, no UI tests)
- Docker image pushed only as `:latest` — no versioned tags, can't roll back
- Docker build only on main push — broken Dockerfile merges undetected
- No quality gates: no lint, no formatting checks
- No release automation

## Design

Adapted from [guzalv/stackrox-co-importer](https://github.com/guzalv/stackrox-co-importer) CI patterns. Key decisions:

1. **Job dependency chain**: `lint` + `test` run in parallel → `build` (Docker) → `release` (only on main).
2. **Lint**: ruff check + ruff format --check. Zero tolerance — every issue fails the build.
3. **UI tests**: run in a separate job with chromium, allowed to be optional initially.
4. **Versioned Docker tags**: SHA-based (`ghcr.io/repo:abc123f`), date-based (`:20260724`), and `:latest`.
5. **Docker build on PRs**: build but don't push — catches Dockerfile breakage early.

### Pipeline diagram

```
PR:    lint ──┐
       test ──┼── build (no push)
              │
main:  lint ──┐
       test ──┼── build+push (SHA + date + latest tags)
```

## Implementation

### Workflow: `.github/workflows/ci.yml`

See the actual file committed alongside this plan.

### Makefile additions

Add `lint` and `fmt` targets:

```makefile
lint: test-deps ## Run linter
	$(VENV)/bin/ruff check server.py tests/

fmt: test-deps ## Check formatting
	$(VENV)/bin/ruff format --check server.py tests/

fmt-fix: test-deps ## Auto-format code
	$(VENV)/bin/ruff format server.py tests/
```

Update `test-deps` to include ruff:

```makefile
test-deps: deps
	$(PIP) install -q pytest httpx ruff
```

### Ruff config

Add minimal `ruff.toml`:

```toml
line-length = 120
target-version = "py312"

[lint]
select = ["E", "F", "W", "I"]

[lint.per-file-ignores]
"tests/*" = ["E501"]
```

## Verification

1. Push a PR — lint, test, and build jobs run. Docker builds but doesn't push.
2. Merge to main — all jobs run plus Docker push with SHA + date + latest tags.
3. `make lint` locally — catches the same issues CI would.
4. Break formatting intentionally — CI fails on the `lint` job.

## Notes

### UI tests in CI

UI tests need chromium + selenium. Two options:
- Run in a separate optional job with `continue-on-error: true` initially
- Skip until the test setup is simplified (e.g., playwright instead of selenium)

Current plan: include as a separate job, non-blocking. Promote to required once stable.

### Future additions (not in this plan)

- **Auto-merge workflow**: enable `gh pr merge --auto --rebase` on PRs (like the reference repo)
- **Release-check cron**: if auto-merge is added, a 15-min cron to dispatch CI for untagged commits on main (works around GitHub's GITHUB_TOKEN event suppression)
- **Semantic versioning**: auto-tag based on commit prefixes
- **Coverage reporting**: upload to Codecov or similar
