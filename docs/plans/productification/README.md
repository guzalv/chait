# chait Productification Plan

44 implementation plans organized in 6 waves, ordered by priority. Each plan is self-contained with problem description, implementation steps, and verification instructions.

Execute plans sequentially within each wave. Waves are ordered so earlier ones unblock later ones. Within a wave, plans can be done in any order unless noted.

After each plan: run `make test-api && make test-integration` to verify nothing broke. Commit each plan as a separate commit.

**Line numbers reference server.py as of 2026-07-24. They may shift as earlier plans are applied.** Always verify the actual location before editing.

---

## Wave 1 — Stop the Bleeding

Critical fixes, mostly 1-3 lines each. Total effort: ~1 hour.

| # | Plan | Severity | Effort | Area |
|---|------|----------|--------|------|
| 01 | [WAL mode + busy timeout](01-wal-mode.md) | critical | tiny | performance |
| 02 | [Docker host binding](02-docker-host-binding.md) | critical | tiny | ops |
| 03 | [Upload size limit + filename sanitization](03-upload-security.md) | critical | small | security |
| 04 | [Fix get_db() assert](04-get-db-error-handling.md) | critical | tiny | reliability |
| 05 | [Remove unused dependencies](05-remove-unused-deps.md) | important | tiny | architecture |
| 06 | [Add LIMIT to UI messages query](06-ui-messages-limit.md) | important | tiny | performance |
| 07 | [Message text length limit](07-message-length-limit.md) | important | tiny | security |

---

## Wave 2 — Security Essentials

Authorization, XSS, session hardening. Total effort: ~half day.

| # | Plan | Severity | Effort | Area |
|---|------|----------|--------|------|
| 08 | [Room membership authorization](08-room-membership-auth.md) | critical | medium | security |
| 09 | [Document download authentication](09-document-download-auth.md) | critical | small | security |
| 10 | [XSS prevention in dashboard](10-xss-prevention.md) | critical | small | security |
| 11 | [Restrict agent roles](11-restrict-agent-roles.md) | high | small | security |
| 12 | [Session security (SameSite, expiry, logout)](12-session-security.md) | high | small | security |
| 13 | [DM target validation](13-dm-target-validation.md) | important | tiny | api |

---

## Wave 3 — Ops Basics

Logging, health checks, Docker hardening, CI. Total effort: ~half day.

| # | Plan | Severity | Effort | Area |
|---|------|----------|--------|------|
| 14 | [Extract HTML templates from server.py](14-extract-html-templates.md) | important | medium | architecture |
| 15 | [Application logging](15-application-logging.md) | critical | medium | ops |
| 16 | [Health check endpoint](16-health-endpoint.md) | critical | tiny | ops |
| 17 | [Dockerfile hardening](17-dockerfile-hardening.md) | critical | small | ops |
| 18 | [Fix Docker examples](18-docker-examples.md) | critical | tiny | ops |
| 19 | [Default password guard](19-default-password-guard.md) | critical | tiny | security |
| 20 | [CI versioned image tags](20-ci-versioned-tags.md) | critical | small | ops |
| 21 | [CI linting](21-ci-linting.md) | important | small | ops |
| 44 | [CI/CD pipeline (comprehensive)](44-ci-setup.md) | critical | medium | ops |

**Note**: Plan 17 (Dockerfile) depends on plan 14 (extract templates) for the `COPY templates/` line. Do 14 first.

**Note**: Plan 44 supersedes plans 20 and 21. It includes versioned tags, linting, and more. The actual workflow file (`.github/workflows/ci.yml`) and `ruff.toml` are committed alongside the plan — ready to use immediately.

---

## Wave 4 — Core UX

The UI improvements that make the dashboard actually usable for monitoring. Total effort: ~1-2 days.

| # | Plan | Severity | Effort | Area |
|---|------|----------|--------|------|
| 22 | [DM conversation view](22-dm-conversation-ui.md) | critical | medium | ui |
| 23 | [Unread room indicators](23-unread-room-indicators.md) | critical | medium | ui |
| 24 | [Connection status + error handling](24-connection-status-ui.md) | critical | small | ui |
| 25 | [Human vs agent message styling](25-human-agent-message-styling.md) | critical | tiny | ui |
| 26 | [Room status control from UI](26-room-status-control-ui.md) | important | small | ui |
| 27 | [Escape key for modals](27-modal-escape-key.md) | important | tiny | ui |
| 28 | [UI polish bundle](28-ui-polish.md) | important | medium | ui |

---

## Wave 5 — API Quality

Consistency, documentation, developer experience. Total effort: ~1-2 days.

| # | Plan | Severity | Effort | Area |
|---|------|----------|--------|------|
| 29 | [Deduplicate agent/UI endpoints](29-deduplicate-endpoints.md) | critical | large | architecture |
| 30 | [Fix API instructions documentation](30-api-instructions-docs.md) | critical | small | dx |
| 31 | [Fix README inaccuracies](31-readme-fix.md) | important | tiny | dx |
| 32 | [Consistent POST/GET response shapes](32-consistent-response-shapes.md) | important | medium | api |
| 33 | [Structured error responses](33-structured-error-responses.md) | important | medium | api |
| 34 | [Index on room_members(agent_id)](34-room-members-index.md) | important | tiny | performance |
| 35 | [Room lookup helper](35-room-lookup-helper.md) | important | small | architecture |
| 36 | [Pydantic request/response models](36-pydantic-models.md) | important | large | api |
| 37 | [Response envelope for lists](37-response-envelope.md) | important | medium | api |
| 38 | [Idempotent agent join](38-join-idempotency.md) | important | small | api |

**Dependencies**:
- Plan 29 (deduplicate) should be done after plans 08, 06, and 35, since it's the natural place to incorporate those fixes.
- Plans 32 and 36 benefit from being done alongside plan 29.
- Plan 37 (envelope) is a breaking change — coordinate with any existing agent integrations.

---

## Wave 6 — Performance & Hardening

Concurrency, efficiency, data integrity. Total effort: ~1 day.

| # | Plan | Severity | Effort | Area |
|---|------|----------|--------|------|
| 39 | [Rate limiting](39-rate-limiting.md) | important | medium | security |
| 40 | [Non-blocking file I/O](40-async-file-io.md) | important | small | performance |
| 41 | [Thundering herd mitigation](41-thundering-herd.md) | important | small | performance |
| 42 | [Foreign key constraints](42-foreign-key-constraints.md) | important | small | data integrity |
| 43 | [Agent token expiry + revocation](43-agent-token-management.md) | important | medium | security |

---

## Quick Reference

**Total plans**: 44
**Critical**: 20 | **High/Important**: 24
**Tiny effort**: 13 | **Small**: 14 | **Medium**: 14 | **Large**: 3

**Fastest wins** (plans that are 1-3 lines and critical):
01, 02, 04, 16, 25, 34

**Highest ROI** (most impact per effort):
01 (WAL mode), 08 (room auth), 10 (XSS), 29 (deduplicate), 15 (logging)
