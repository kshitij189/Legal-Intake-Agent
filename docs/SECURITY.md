# Security, privacy and tenant-isolation review (Phase 8)

Scope: the local demo deployment (Docker Compose on one machine, bound to 127.0.0.1, fictional
data only). Each control below says how it is enforced and which test proves it. The gaps that
remain before any real use are listed at the end.

## Data handled

| Data | Where | Notes |
|---|---|---|
| Caller statements (name, phone, email, matter, dates) | `intake_records.fields` (JSONB, per-field provenance) | Fictional only. Sent to Gemini/Groq during a conversation. |
| Transcripts | `transcript_turns` | Kept only if the firm retains them **and** the caller was told so in the greeting (`call_sessions.consent`). Deleted after `privacy.transcript_retention_days` (default 90) by `scripts/purge_transcripts.py`. |
| Audio | nowhere | Never recorded or stored. |
| Staff credentials | `staff_members.password_hash` (scrypt), `staff_sessions.token_hash` (SHA-256) | Plaintext passwords and tokens are never stored. |
| Audit trail | `audit_events` | Ids and statuses only, never field values or transcript text. |

## Controls

**Tenant isolation**
- Every domain query is filtered by `firm_id`. The firm comes from the session context (agent) or
  the staff session (API), never from model arguments or request bodies.
- Composite foreign keys `(firm_id, id)` stop child rows from pointing into another tenant.
- Cross-firm access returns "not found" (404), not "forbidden".
- Tests: `test_api.py::test_every_id_route_is_tenant_scoped` sweeps all 8 id-taking routes and
  5 lists; the other firm's records come back "not found", unchanged and unlisted. There are also
  domain-level cross-tenant tests from Phase 1.

**Authentication and authorization**
- Only `POST /auth/login` is open on the staff API (`test_every_staff_route_requires_a_session`).
  `/openapi.json`, `/docs` and `/redoc` are disabled.
- Sessions: 32-byte random tokens with an 8 h TTL, revoked on logout. They travel in an
  HttpOnly, SameSite=Strict cookie (`Secure` via `DASHBOARD_COOKIE_SECURE` behind HTTPS) or a
  Bearer header.
- All login failures produce the same 401, and a dummy scrypt check equalizes timing for unknown
  emails.
- Throttling: after 5 failed logins per (firm, email) within 15 minutes, further attempts get 429
  with `Retry-After`. The key is the account, not the IP, because behind the dashboard proxy every
  client is 127.0.0.1 and forwarded-for headers can be spoofed. The trade-off is that an attacker
  can lock one account for 15 minutes.
- Role rules live in the domain (`domain/actors.py::authorize`), not in routes or prompts:
  - only LAWYER / INTAKE_COORDINATOR may accept or resolve escalations, verify fields or close
    intakes;
  - only SYSTEM may mark a handoff FAILED;
  - the agent can never set `VERIFIED_BY_SYSTEM`.
- CSRF: SameSite=Strict cookies, JSON-only bodies, same-origin proxy and no CORS. The login page
  redirects only to same-site paths, which closes the open-redirect path.

**Model and tool boundary**
- The model sees exactly nine tools with strict Pydantic schemas (`extra="forbid"`). It has no
  SQL, shell, file or HTTP tools. Arguments are re-validated on our side, and ids and idempotency
  keys are derived in code.
- Outcomes the model reports are checked against the database:
  - a reply guard blocks booking and acceptance claims that the database does not support;
  - post-commit verification runs before a booking is confirmed;
  - the omission check catches unsaved deadlines.

  Evidence: Phase 6 adversarial scenarios A01–A05, and the negative controls in
  `test_evaluation_harness.py`.

**Secrets**
- API keys and the demo staff password live only in the git-ignored `.env` (`git check-ignore .env`).
  They are typed `SecretStr` and never logged (grep: no `get_secret_value()` near a log call).
- `.env.example` holds placeholders; the demo password line is commented out, so a copied example
  never creates a known login.
- The LiveKit `devkey/secret` credentials are LiveKit's local dev-mode defaults, bound to 127.0.0.1.

**Logging and observability**
- JSON-lines logs (`app/observability/logging.py`). Structured `extra` fields are kept, and API
  responses carry an `X-Request-ID`.
- The access log records method, path, status and duration. It never records bodies, query
  values, transcript text or contact details (CLAUDE.md §10).

**Retention and deletion**
- Transcript purge per firm policy, audited as `transcripts.purged` (counts only). Covered by a
  test in `test_transcripts.py`.
- Finished evaluation and demo firms are deactivated; their leftover workflow requests are never
  dispatched (`test_workflow_outbox.py`).

**Reliability-related safety** (Phases 5–6)
- Retries are bounded, side effects are idempotent (booking key, notification `dedupe_key`), and
  the transactional outbox prevents lost or duplicate workflow starts.
- Fault-injection scenarios: calendar timeout, reviewer channel down or flaky, LLM and database
  outages.

## Gaps before any real deployment

1. **Unauthenticated voice token endpoint** (`POST /api/v1/voice/token`): anyone who can reach
   127.0.0.1:8000 can start a call. Real deployments need a caller-facing gateway (telephony or an
   authenticated widget) and per-number rate limits.
2. **TLS** everywhere (API, dashboard, LiveKit, Temporal, PostgreSQL). Also needed: secrets in a
   secret manager instead of `.env`, and key rotation.
3. **Staff identity:** MFA or SSO, password reset, staff administration, and a shared throttle
   store for multi-instance deployments.
4. **Least privilege:** the app connects as the database owner. Production needs separate
   migration and runtime roles, and Temporal/LiveKit authentication (both run in dev mode).
5. **Third-party data processing:** conversations go to Gemini/Groq. Real client data needs data
   processing agreements, region choices, or a self-hosted model.
6. **Right to erasure** for a whole intake (beyond transcript retention) is not implemented.
   Neither are backups/restore drills or dependency vulnerability scanning in CI.
7. **Langfuse tracing** (optional in the blueprint) is not added. Structured logs and the audit
   trail are the observability surface.
