# Change Log

Chronological engineering log. Entries record what was actually done and actually observed.
Newest entries at the bottom.

---

## 2026-10-11 — Project documentation setup

**Scope:** documentation only; no application code.

**Repository inspection**
- Working directory `Legal_Intake_agent` was empty; not a git repository.
- Tooling found on the dev machine: Python 3.13.2 and 3.14 (`py` launcher), Docker 29.1.3 with Compose
  v2.40.3 (daemon running), Node 22.20.0, git 2.44, psql 17.9. A local `postgresql-x64-17` Windows
  service is listening on port 5432.

**Files created**
- `CLAUDE.md` — persistent engineering instructions derived from the blueprint.
- `IMPLEMENTATION_PROGRESS.md` — phase checklist; all phases `NOT_STARTED`.
- `CHANGE_LOG.md` — this file.

**Decisions**
- Python 3.13 rather than 3.14: wider binary-wheel availability for the voice stack
  (faster-whisper/CTranslate2, LiveKit).
- Compose PostgreSQL will publish host port 5433 because 5432 is taken by the local service.
- Synchronous SQLAlchemy 2.x with psycopg 3; async callers use `asyncio.to_thread`. Rationale: simpler,
  deterministic transactions and tests; psycopg's async mode needs a selector event loop on Windows.
- Enums stored as `VARCHAR` + `CHECK` (no native PG enums) to keep Alembic migrations simple.
- Field status `DECLINED_BY_CALLER` added to the blueprint's status list, because the blueprint's
  "caller refuses a field" scenario needs a distinct, non-fabricated state.
- Proposed (pending your confirmation): REST API built in Phase 7 with authentication — see
  `IMPLEMENTATION_PROGRESS.md` → Outstanding decisions.

**Tests executed:** none (no code yet).

---

## 2026-10-11 — Phase 1: Domain and database foundation

**Scope:** data model, migrations, deterministic business rules, seed data, manual demo. No LLM,
voice, API, workflow or frontend code.

**Added**
- Infrastructure: `docker-compose.yml` (PostgreSQL 17 on host port 5433), `infra/postgres/init/`
  (creates `legal_intake_test`), `.env.example`, `.gitignore`, `.gitattributes`, `ruff.toml`.
- `backend/pyproject.toml`; `app/config.py` (pydantic-settings, reads repo-root `.env`);
  `app/db/{base,session,seed}.py`.
- Models (`app/models/`): `Firm`, `StaffMember`, `AvailabilitySlot`, `Appointment`, `IntakeRecord`,
  `Escalation`, `AuditEvent`; Alembic migration `cad5c81e1178` (plus hand-added
  `intake_reference_seq`).
- Schemas: `FirmConfig` (questions, rules, scheduling, cross-validation), `FieldEntry`.
- Domain: `policy_engine`, `transitions`, `actors` (authorization table), `audit`, `errors`,
  `clock`, `idempotency`, `firm_config_service`, `intake_service`, `appointment_service`,
  `escalation_service`.
- Scripts: `scripts/seed_demo_data.py`, `scripts/phase1_demo.py`.
- Tests: `backend/tests/unit/` (config, policy engine, transitions) and
  `backend/tests/integration/` (migrations + seed, intake, appointments incl. concurrency,
  escalations, end-to-end exit criterion).

**Decisions and rationale**
- Firm policy (questions, escalation rules, scheduling limits) is JSONB on `firms.config`, validated
  by Pydantic, instead of extra tables: it keeps to the blueprint's table list and is versioned as one
  document. Rule kinds: `deadline_within_days` (past dates also match) and `field_value_in`.
- Intake fields are JSONB `{key: {value, status, note, source, updated_at}}` keyed by question key;
  writes lock the intake row (`FOR UPDATE`) to avoid lost updates.
- A deadline with any stored value (including `PENDING_HUMAN_REVIEW`) triggers the deadline rule:
  uncertainty should reach a human, not suppress escalation.
- `CONFIGURED_RULE` escalations are re-evaluated against stored state, so the model cannot escalate
  on an invented rule or invented facts. `UNABLE_TO_COMPLETE` gets fixed priority `HIGH`.
- Booking: lock the intake → idempotency replay → one booking per intake → lock the slot →
  re-validate (status, notice window, active lawyer, practice area matches matter category). Partial
  unique indexes are the database backstop.
- Matter category `OTHER` has no lawyer practice area, so no slots are offered; it routes to review
  through the `outside_intake_workflow` rule.
- Tenant isolation through composite `(firm_id, id)` foreign keys plus firm-scoped queries;
  cross-tenant lookups return `NotFound`.
- Authorization table: the agent can create, update and book, but never verify fields, close
  intakes, or accept/resolve escalations. Administrators configure but don't review.
- Audit `details` contain IDs/statuses only (no contact values); `audit_events.seq` identity column
  added because `now()` is constant within a transaction, so ordering by timestamp was ambiguous.
- REST API deferred (Outstanding decision 1 in `IMPLEMENTATION_PROGRESS.md`).
- Dependencies added: sqlalchemy 2.0.54, alembic 1.20.0, psycopg[binary] 3.3.6, pydantic 2.14.0,
  pydantic-settings 2.15.0, tzdata 2026.5; dev: pytest 8.4.2, ruff 0.17.0, mypy 1.20.2.

**Issues found and fixed during the phase**
- Audit ordering ambiguity (see `seq` above). Found while writing the timeline assertions; the
  migration was regenerated before it was applied anywhere persistent.
- The editable install ran before `backend/app/` existed, so the scripts raised
  `ModuleNotFoundError: app`. Fixed by re-running `pip install -e "backend[dev]"` (noted in
  CLAUDE.md).
- Ruff run from the repo root ignored `backend/pyproject.toml` and sorted `alembic` as first-party;
  moved the config to the root `ruff.toml` with `known-third-party = ["alembic"]`.
- mypy strict: a reassigned `Insert`/`ReturningInsert` variable in `seed.py`; restructured.
- Alembic `path_separator` deprecation warning; set `path_separator = os`.

**Tests and validations executed (actual results)**
- `pytest` (backend/): 111 passed, 0 failed (73 unit + 38 integration on PostgreSQL 17).
- Mutation check: with `for_update=False` on the intake lock in `book_consultation`,
  `test_concurrent_retries_with_same_key_create_one_appointment` failed (1 failed, 1 passed);
  the code was restored and the full suite re-run green.
- `ruff check backend scripts`: all checks passed; `ruff format --check`: 45 files formatted.
- `mypy backend/app` (strict): success, 28 source files.
- `alembic upgrade head` (main DB) OK; `alembic check`: no new upgrade operations detected.
- `scripts/seed_demo_data.py` twice: 120 slots created, then 0.
- `scripts/phase1_demo.py`: intake INT-1001 created; `imminent_deadline` matched; booking replay
  returned the same appointment; escalation `ACCEPTED` by the coordinator; SQL inspection confirmed
  1 appointment, slot `BOOKED`, escalation `ACCEPTED/URGENT`, staff 3 lawyers + 1 coordinator +
  1 admin. The main database now holds this fictional demo data.

**Known issues / limitations:** see Phase 1 → "Known limitations" in `IMPLEMENTATION_PROGRESS.md`.

**Next:** Phase 2 — Text-based intake agent (not started; awaiting authorization).
