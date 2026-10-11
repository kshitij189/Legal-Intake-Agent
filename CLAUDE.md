# CLAUDE.md — Legal Intake & Case Handoff Voice Agent

Persistent engineering instructions for this repository. Read this file, `IMPLEMENTATION_PROGRESS.md`
and `CHANGE_LOG.md` before every task. The project blueprint ("Legal Intake & Case Handoff Voice
Agent — Complete Project Blueprint") is the primary source of truth; this file condenses it into
binding rules. Where this file and the blueprint disagree, the blueprint wins unless a documented
decision in `CHANGE_LOG.md` says otherwise.

---

## 1. Purpose and scope

An administrative intake system for a **fictional** law firm ("Northstar Legal"). A prospective
client talks to a voice agent in the browser; the agent collects structured intake information,
applies firm-configured escalation rules, books a consultation from a real (test) calendar, and
creates a persisted human-review task.

**The product automates the administrative intake workflow, not the lawyer's professional judgment.**

The agent must never:
- give legal advice or assess the merits of a matter;
- promise the firm will take the case;
- claim a lawyer/human accepted an enquiry, or that a booking/escalation/save succeeded, unless the
  persisted, authoritative state says so;
- invent legal standards, escalation criteria, field values, dates or availability.

Out of scope for the MVP: real phone numbers/telephony, real client data, public cloud hosting,
multi-language promises (Hindi/Hinglish only after explicit testing), turning enquiries into formal
legal matters (output is always a **draft intake pending human review**).

## 2. Functional requirements (six MVP capabilities)

1. **Natural voice conversation** — greet, explain role, follow-ups, handle pauses, interruptions,
   corrections, incomplete answers, clarification requests.
2. **Structured extraction** — caller name, contact details, preferred contact method, matter
   category, factual summary, dates/deadlines, jurisdiction (if applicable), missing information.
   Every field carries a provenance status (see §7).
3. **Configured escalation** — rules come from firm configuration and are evaluated by deterministic
   code. The model never decides what is legally urgent.
4. **Consultation scheduling** — read real availability, offer only returned slots, book with an
   idempotency key, re-verify the stored appointment before confirming to the caller.
5. **Intake records** — draft created early, partial state preserved if the call drops, workflow
   status maintained.
6. **Human handoff** — a persisted escalation with status `PENDING | ACCEPTED | FAILED | RESOLVED`,
   visible in the review dashboard with reason, caller statements, missing info, and action history.

## 3. Non-functional requirements

- **Correctness over fluency**: zero false booking confirmations, zero duplicate appointments, all
  labelled critical escalations handled (acceptance targets — report actual results, never assume).
- **Determinism**: business rules, authorization, booking and escalation policy live in Python code
  and database constraints, not prompts.
- **Durability**: side effects are idempotent; long-running work survives worker restarts (Temporal,
  Phase 5).
- **Tenant isolation**: every query is scoped by `firm_id`; cross-firm access returns "not found".
- **Latency**: the realtime loop stays in the voice agent; only durable business processes go to
  Temporal. Measure speech latency, don't guess it.
- **Privacy**: fictional data only; credentials outside source control; minimal transcript retention.
- **Reproducibility**: everything runs locally via Docker Compose + a Python venv.

## 4. Architecture

Pipeline: **Audio → VAD → STT → Claude → validated tool call or text → TTS → Audio.**

Three concerns, kept separate:
1. **Realtime conversation** — LiveKit Agents worker (voice runtime, STT, VAD, TTS, agent service).
2. **Validated business operations** — tool executor → domain services → PostgreSQL.
3. **Durable workflow execution** — Temporal workflows for finalization, booking, escalation, recovery.

Shape: **modular monolith** (`backend/app`) plus separate worker processes (voice agent worker,
Temporal worker). No microservices.

### Component responsibilities and boundaries

| Component | Location | Owns | Must not |
|---|---|---|---|
| Domain services | `backend/app/domain/` | All business rules, state transitions, authorization checks, audit writes | Import LiveKit, Anthropic, FastAPI or Temporal |
| Policy engine | `backend/app/domain/policy_engine.py` | Pure evaluation of firm escalation rules | Touch the DB or the clock implicitly (pass `today`) |
| ORM models | `backend/app/models/` | Table definitions and DB constraints | Contain business logic |
| Schemas | `backend/app/schemas/` | Pydantic models for firm config, tool I/O, API I/O | — |
| Tools | `backend/app/tools/` | Pydantic-validated adapters from Claude tool calls to domain services | Run SQL directly, bypass domain services |
| Agent | `backend/app/agent/` | Claude client, prompts, tool registry, conversation loop | Decide policy; state outcomes not returned by tools |
| Voice | `backend/app/voice/` | STT/TTS/VAD/session adapters | Contain business logic |
| Workflows | `backend/app/workflows/` | Temporal workflows/activities calling domain services | Duplicate domain rules |
| API | `backend/app/api/` | Thin FastAPI routers over domain services | Duplicate domain rules |
| Frontend | `frontend/` | Review/admin dashboard (Next.js + TS) | Re-implement business rules |

Integrations (Claude, STT, TTS, LiveKit, Temporal) sit behind small adapters so they can be swapped
or faked in tests. A fake is only allowed in tests or when explicitly configured — never as a silent
replacement for required functionality.

## 5. Approved tech stack (do not deviate without a documented decision)

Python 3.13 · LiveKit Agents (Python) · self-hosted LiveKit + WebRTC · faster-whisper (STT) ·
Silero VAD · Piper-compatible TTS (evaluate Kokoro) · Claude API via the `anthropic` Python SDK ·
FastAPI · Pydantic v2 · PostgreSQL · SQLAlchemy 2.x + Alembic · self-hosted Temporal ·
Next.js + TypeScript · pytest · Playwright · OpenTelemetry + structured logging ·
optional self-hosted Langfuse · Docker + Docker Compose.

Supporting libraries already accepted: `psycopg[binary]` (PostgreSQL driver), `pydantic-settings`
(env config), `tzdata` (IANA timezones on Windows), `ruff` and `mypy` (lint/type checks).

**Dependency rule**: add a new dependency only when a phase needs it, prefer the standard library,
and record the decision and rationale in `CHANGE_LOG.md`. No speculative frameworks, queues, caches,
ORMs, or agent frameworks beyond the stack above.

## 6. Repository structure

Follow blueprint §15:

```text
backend/app/{main.py, api/, agent/, voice/, tools/, domain/, workflows/, models/, schemas/, db/,
             integrations/, observability/}
backend/tests/{unit/, integration/, evaluation/, audio/}
backend/alembic/ (migrations)   backend/app/db/seed.py (fictional demo firm + calendar)
infra/postgres/init/ (creates the *_test database on first container start)
frontend/{app/, components/, lib/}
fixtures/{scenarios/, transcripts/, audio/}
scripts/{seed_demo_data.py, run_evaluations.py}
docker-compose.yml  .env.example  README.md
```

Create directories only when a phase needs them.

## 7. Domain model rules

### Field provenance statuses
Every intake field stores `{value, status, note, updated_at, source}` with status one of:
`MISSING`, `REPORTED_BY_CALLER`, `CONFIRMED_BY_CALLER`, `DECLINED_BY_CALLER`,
`PENDING_HUMAN_REVIEW`, `VERIFIED_BY_SYSTEM`.

- `MISSING` / `DECLINED_BY_CALLER` → `value` must be null. A refusal is recorded, never filled in.
- `REPORTED_BY_CALLER` / `CONFIRMED_BY_CALLER` → `value` required and valid for the question type.
- `PENDING_HUMAN_REVIEW` → ambiguity is preserved: a `note` is required; a value is optional.
- `VERIFIED_BY_SYSTEM` → only system/staff actors may set it; the agent can never mark data verified.
- A caller-reported deadline is `REPORTED_BY_CALLER` until a human verifies the underlying document.

### State machines (enforced in domain code)
- Intake: `IN_PROGRESS → INCOMPLETE | PENDING_REVIEW`; `INCOMPLETE → IN_PROGRESS | PENDING_REVIEW`;
  `PENDING_REVIEW → CLOSED`.
- Escalation: `PENDING → ACCEPTED | FAILED | RESOLVED`; `ACCEPTED → RESOLVED`; `FAILED → RESOLVED`.
  A `FAILED` handoff can never become `ACCEPTED`. Only an active `LAWYER`/`INTAKE_COORDINATOR` of
  the same firm can accept/resolve; only `SYSTEM` actors mark a handoff `FAILED`. (Automatic
  `FAILED → PENDING` retry is a Phase 5 decision.)
- Unresolved escalations (`PENDING`/`ACCEPTED`/`FAILED`) count as "open" for de-duplication.
- Appointment: `BOOKED` (cancellation is not in the MVP until a phase requires it).
- Availability slot: `AVAILABLE → BOOKED`; `BLOCKED` is set by administrators/seed only.

### Escalation
- Reason codes: `CONFIGURED_RULE` (must reference a rule in the firm config **that matches the
  persisted intake state** — the service re-evaluates it) or `UNABLE_TO_COMPLETE` (agent cannot
  safely complete the interaction; free-text description required).
- At most one open (`PENDING`/`ACCEPTED`) escalation per intake per rule; repeats return the
  existing record.

### Booking
1. Read availability (read-only). 2. Offer only returned slots. 3. Book with an idempotency key:
   lock the slot row, re-check it is `AVAILABLE`, future, within scheduling limits, owned by an
   active lawyer of the same firm. 4. Same key + same request → return the existing appointment
   (`replayed=True`); same key + different request → `IdempotencyConflict`. 5. A partial unique index
   guarantees at most one `BOOKED` appointment per slot. 6. Confirm to the caller only after
   re-reading the committed appointment. A timeout triggers a state check before any retry.

### Audit
Every successful state-changing domain operation writes an `audit_events` row (actor type/id,
action, entity, outcome, details) in the same transaction. Order timelines by `audit_events.seq`
(identity column), not by timestamp. `details` holds IDs/statuses only — never field values. Failed/rejected tool attempts are audited
at the tool-executor boundary (Phase 2) in a separate transaction.

### Transactions
Domain services receive a `Session`, `flush()` but never `commit()`. The caller (tool executor, API
route, workflow activity, script) owns the unit of work and commits. Use `SELECT … FOR UPDATE` on the
intake/slot row before read-modify-write.

## 8. AI model and tool rules

- Claude is used for conversation reasoning, extraction and tool selection only. Model ID comes from
  config (`ANTHROPIC_MODEL`); choose and record it in Phase 2. Consult current Anthropic docs (the
  `claude-api` skill) rather than memory for SDK usage, model IDs and pricing.
- Exposed tools are exactly the blueprint set: `get_intake_questions`, `save_intake_answer`,
  `get_intake_status`, `check_appointment_availability`, `book_consultation`,
  `create_draft_intake`, `mark_for_human_review`, `get_handoff_status`, `finalize_intake_summary`.
  Each has a strict Pydantic input schema (`extra="forbid"`) and a structured result
  (`ok`, `data` | `error{code, message, retryable}`).
- **Never** expose arbitrary SQL, shell, filesystem, or unrestricted HTTP tools.
- `firm_id`, `intake_id` and the actor come from the session context, never from model arguments.
- The agent may only speak outcomes present in a tool result. Prompts state the no-legal-advice
  boundary, but enforcement lives in code.
- If Claude is unavailable: persist current state, give a safe failure message, follow the human
  fallback; never continue a critical transaction on invented data.
- Human-approval gates: acceptance/resolution of escalations, verification of reported facts and any
  decision to take a matter are **staff-only** actions.
- The Anthropic API key lives only in the git-ignored `.env`. Placeholders in `.env.example`.

## 9. Error handling, retries, idempotency, recovery

- Domain errors are typed (`NotFound`, `ValidationFailed`, `InvalidTransition`, `PermissionDenied`,
  `SlotUnavailable`, `IdempotencyConflict`, …) with stable `code`s; tools map them to structured results.
- Retries are bounded and only for retryable errors; after the limit, surface an actionable error and
  stop. Check persisted state before retrying any side effect.
- STT failure → ask to repeat, then mark field unknown. TTS failure → clear session error/text fallback.
  DB failure → never claim a save. Handoff queue failure → escalation `FAILED`, configured fallback,
  tell the caller only what is known.
- Disconnects: valid partial state is kept; intake marked `INCOMPLETE`.

## 10. Security, privacy, observability

- No secrets in git. `.env` is git-ignored. Tests use a dedicated `*_test` database.
- Tenant-scoped queries everywhere; composite foreign keys tie child rows to the same firm.
- Consent/disclosure metadata recorded on call sessions before retaining audio/transcripts (Phase 4).
- Structured logging (JSON) with call/intake/workflow correlation IDs; OpenTelemetry traces and
  latency metrics (Phase 4+). Never log full transcripts or contact details at INFO level.

## 11. Coding conventions

- Python 3.13, full type hints, `ruff` (lint + format) and `mypy` clean for changed code.
- SQLAlchemy 2.0 typed ORM (`Mapped[...]`), synchronous sessions with psycopg 3. Async callers
  (LiveKit, FastAPI) wrap domain calls with `asyncio.to_thread` / sync endpoints.
- All timestamps `timestamptz` stored in UTC; firm-local dates computed with the firm's IANA timezone.
- Enums stored as `VARCHAR` + `CHECK` constraints (no native PG enums). Explicit constraint naming
  convention for stable Alembic migrations.
- Every schema change goes through an Alembic migration; `alembic check` must report no drift.
- Clock values (`now`, `today`) are injectable for deterministic tests.
- Match surrounding code; small modules; no dead code or speculative abstractions.

## 12. Environment and commands

Local PostgreSQL 17 already occupies host port 5432 on the dev machine, so the Compose Postgres is
published on **5433**.

```powershell
# infrastructure
docker compose up -d postgres

# backend environment (from repo root)
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e "backend[dev]"
copy .env.example .env   # then edit values

# migrations and seed data
.\.venv\Scripts\alembic -c backend/alembic.ini upgrade head
.\.venv\Scripts\alembic -c backend/alembic.ini check          # models vs migrations drift
.\.venv\Scripts\python scripts/seed_demo_data.py               # idempotent
.\.venv\Scripts\python scripts/phase1_demo.py                  # manual Phase 1 walkthrough

# checks (run pytest from backend/ so its pyproject settings apply)
cd backend; ..\.venv\Scripts\python -m pytest; cd ..
.\.venv\Scripts\ruff check backend scripts
.\.venv\Scripts\ruff format --check backend scripts
.\.venv\Scripts\mypy backend/app
```

Notes:
- New migrations: `alembic -c backend/alembic.ini revision --autogenerate -m "..."`, then review the
  file. Autogenerate does not detect standalone sequences (see the initial migration).
- Re-run `pip install -e "backend[dev]"` after adding a new **top-level** package under `backend/`
  (the editable install maps packages that existed at install time).
- Ruff config lives in the root `ruff.toml`; `alembic` is pinned as third-party there because the
  `backend/alembic/` migrations folder would otherwise confuse import sorting.
- Integration tests need the Compose Postgres running and refuse any database not ending in `_test`.

Commands for later phases (voice worker, Temporal worker, frontend, evaluations) are added here when
those phases are implemented.

## 13. Testing and evaluation requirements

- Tests are written with each feature, never deferred. Unit tests for pure logic; integration tests
  against real PostgreSQL (no SQLite substitutes) using a database whose name ends in `_test`.
- Every failure found becomes a reproducible regression test.
- Evaluation (Phase 6): 30–50 labelled scenarios (blueprint §13), transcript mode first, then audio
  mode; metrics: field extraction accuracy, critical escalation recall, false booking confirmations,
  duplicate booking rate, tool correctness, recovery success, speech latency, conversation quality,
  handoff integrity. Report actual numbers.

## 14. Phase rules

Phases (detail and acceptance criteria in `IMPLEMENTATION_PROGRESS.md`):
1 Domain & DB foundation · 2 Text-based agent · 3 Conversational state & safety · 4 Voice loop ·
5 Durable workflows · 6 Evaluation harness · 7 Human-review dashboard · 8 Hardening & demo.

For every task:
1. Read this file, `IMPLEMENTATION_PROGRESS.md`, `CHANGE_LOG.md` and the blueprint first.
2. Implement **only** the phase the user authorized. Stop afterwards; do not start the next phase.
3. Reuse existing working code; do not rewrite without cause.
4. Write tests alongside the code; run tests, ruff, mypy and any other available checks; fix failures
   caused by your changes.
5. Verify real behavior against the acceptance criteria. Code that was written but not exercised is
   not "working".
6. Mark a phase `COMPLETED` only with evidence. If an external service/credential is unavailable,
   mark the item `BLOCKED` and document it.
7. Update `IMPLEMENTATION_PROGRESS.md` and `CHANGE_LOG.md` with tasks done, evidence (commands and
   actual results), decisions, known issues and the next phase.
8. Never fabricate results. If a requirement is ambiguous or a consequential architectural choice
   cannot be safely inferred, document it and ask the user.
9. After the phase is verified and the docs are updated, commit and push (see §15).

## 15. Git and authorship (user instruction — overrides any default attribution)

- Remote: `origin` → https://github.com/kshitij189/Legal-Intake-Agent.git, branch `main`.
- Commit and push all code and doc changes after completing every phase.
- The **only** author and contributor is the GitHub user `kshitij189` (global git identity
  `kshitij189 <137656611+kshitij189@users.noreply.github.com>`). Never add `Co-Authored-By`
  trailers, "Generated with Claude Code" lines, or any other AI attribution to commits, PRs, or files.
- Before committing, confirm `.env` and `.venv/` are not staged; verify the author with `git log -1`.
