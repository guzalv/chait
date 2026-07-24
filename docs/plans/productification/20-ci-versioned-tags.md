# 20 — CI: Versioned Docker Image Tags

**Severity**: critical
**Area**: ops
**Effort**: small

## Problem

`.github/workflows/ci.yml` (if it exists) only pushes `:latest` tag. No way to roll back to a known-good version. `docker pull` always gets head-of-main.

## Implementation

Read the existing CI file first. Then update the Docker push step to include SHA-based tags:

```yaml
- name: Build and push Docker image
  if: github.ref == 'refs/heads/main'
  run: |
    echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin
    docker build -t ghcr.io/${{ github.repository }}:latest \
                 -t ghcr.io/${{ github.repository }}:${{ github.sha }} \
                 -t ghcr.io/${{ github.repository }}:$(date +%Y%m%d) \
                 .
    docker push ghcr.io/${{ github.repository }} --all-tags
```

This gives three tags per push:
- `:latest` — always points to newest
- `:abc123def` — immutable SHA tag for pinning
- `:20260724` — date tag for human readability

### Also: build on PRs (don't push)

Add a separate step that runs on all branches:

```yaml
- name: Test Docker build
  run: docker build -t chait-test .
```

## Verification

1. Push to main — check ghcr.io packages page shows multiple tags.
2. Open a PR — Docker build runs but no push.
