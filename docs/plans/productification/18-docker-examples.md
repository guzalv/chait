# 18 — Fix Docker Examples (Volume Mount, Credentials)

**Severity**: critical
**Area**: ops
**Effort**: tiny

## Problem

1. `Makefile:32` — `docker run` has no volume mount. Data lost on container restart.
2. `Makefile:32` and `README.md` — hardcode `CHAIT_HUMAN_PASS=changeme` as the example, encouraging insecure deployments.

## Implementation

### Makefile

Change the `docker` target:

```makefile
docker: ## Build and run via Docker
	docker build -t chait .
	@echo "Starting chait with persistent data volume..."
	docker run -p 3100:3100 \
		-v chait-data:/data \
		-e CHAIT_HUMAN_USER=$${CHAIT_HUMAN_USER:-admin} \
		-e CHAIT_HUMAN_PASS=$${CHAIT_HUMAN_PASS:?Set CHAIT_HUMAN_PASS env var} \
		chait
```

The `${CHAIT_HUMAN_PASS:?...}` syntax makes the shell error out if the variable is unset.

### README.md

Update the Docker quick-start section to include volume mount and not hardcode the password:

```bash
docker run -p 3100:3100 \
  -v chait-data:/data \
  -e CHAIT_HUMAN_USER=admin \
  -e CHAIT_HUMAN_PASS=$(openssl rand -base64 12) \
  ghcr.io/guzalv/chait
```

## Verification

1. `CHAIT_HUMAN_PASS=secret make docker` — starts with volume.
2. `make docker` without env var — errors with message to set password.
3. Stop and restart container — data persists.
