# 21 — CI: Add Linting

**Severity**: important
**Area**: ops, code quality
**Effort**: small

## Problem

CI only runs `test-api` and `test-integration`. No linting, no type checking, no security scanning. Style issues and type errors ship silently.

## Implementation

### Step 1: Add ruff to test deps

In Makefile, update `test-deps`:

```makefile
test-deps: deps
	$(PIP) install -q pytest httpx ruff
```

### Step 2: Add lint target to Makefile

```makefile
lint: test-deps ## Run linter
	$(VENV)/bin/ruff check server.py tests/
```

### Step 3: Add to CI workflow

Add a step before tests:

```yaml
- name: Lint
  run: make lint
```

### Step 4: Optional ruff config

Create `ruff.toml` or add to `pyproject.toml`:

```toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]  # line length handled separately
```

## Verification

1. `make lint` — passes (fix any existing issues first).
2. CI runs lint step.

## Notes

Start with basic rules (E, F, W) — don't boil the ocean with every linting rule. Add stricter rules incrementally.
