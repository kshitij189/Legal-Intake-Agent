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

Pipeline: **Audio → VAD → STT → LLM (Gemini, fallback Groq) → validated tool call or text → TTS → Audio.**

Three concerns, kept separate:
1. **Realtime conversation** — LiveKit Agents worker (voice runtime, STT, VAD, TTS, agent service).
2. **Validated business operations** — tool executor → domain services → PostgreSQL.
3. **Durable workflow execution** — Temporal workflows for finalization, booking, escalation, recovery.

Shape: **modular monolith** (`backend/app`) plus separate worker processes (voice agent worker,
Temporal worker). No microservices.

### Component responsibilities and boundaries

| Component | Location | Owns | Must not |
|---|---|---|---|
| Domain services | `backend/app/domain/` | All business rules, state transitions, authorization checks, audit writes | Import LiveKit, LLM SDKs, FastAPI or Temporal |
| Policy engine | `backend/app/domain/policy_engine.py` | Pure evaluation of firm escalation rules | Touch the DB or the clock implicitly (pass `today`) |
| ORM models | `backend/app/models/` | Table definitions and DB constraints | Contain business logic |
| Schemas | `backend/app/schemas/` | Pydantic models for firm config, tool I/O, API I/O | — |
| Tools | `backend/app/tools/` | Pydantic-validated adapters from LLM tool calls to domain services | Run SQL directly, bypass domain services |
| Agent | `backend/app/agent/` | LLM adapters (Gemini, Groq) + fallback chain, prompts, tool registry, conversation loop | Decide policy; state outcomes not returned by tools |
| Voice | `backend/app/voice/` | STT/TTS/VAD/session adapters | Contain business logic |
| Workflows | `backend/app/workflows/` | Temporal workflows/activities calling domain services | Duplicate domain rules |
| API | `backend/app/api/` | Thin FastAPI routers over domain services | Duplicate domain rules |
| Frontend | `frontend/` | Review/admin dashboard (Next.js + TS) | Re-implement business rules |

Integrations (LLM, STT, TTS, LiveKit, Temporal) sit behind small adapters so they can be swapped
or faked in tests. A fake is only allowed in tests or when explicitly configured — never as a silent
replacement for required functionality.

## 5. Approved tech stack (do not deviate without a documented decision)

Python 3.13 · LiveKit Agents (Python) · self-hosted LiveKit + WebRTC · faster-whisper (STT) ·
Silero VAD · Piper-compatible TTS (evaluate Kokoro) · **Gemini (`google-genai` SDK) primary +
Groq (`groq` SDK) fallback** (replaces the blueprint's Claude API, see §8) ·
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

- **LLM providers: Gemini primary, Groq fallback** (user decisions 2026-10-11). They replace the
  blueprint's Claude API, because no Anthropic key is available. The LLM is used for conversation
  reasoning, extraction and tool selection only.
- `LLM_PROVIDERS` is an ordered model chain of `provider[:model]` entries. The default is
  `gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b`.
  Free-tier quotas are **per model**: `gemini-3.8-flash` allows only **20 requests/day** (measured
  2026-10-11), and one scenario needs about 15 requests. `FallbackLLMClient` tries each entry per
  model call and moves on when an entry raises `LLMUnavailable`. An entry whose 429 asks to wait
  more than 60 s (quota exhausted) is skipped until the stated reset. Entries whose provider key is
  missing or `replace-me` are skipped. Only the **last** entry waits out short 429s, up to
  `LLM_RATE_LIMIT_MAX_WAIT_SECONDS`. When every provider
  fails, the conversation runs the safe fallback: keep state, queue an `UNABLE_TO_COMPLETE`
  review, end the session `FAILED`.
- Gemini: official `google-genai` SDK (native function calling, not the beta OpenAI-compatible
  layer). `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.8-flash`, a stable Flash model).
  Thought signatures: the adapter returns Gemini's `Content` as `provider_state`, keyed by the
  model's chain name (`gemini:<model>`), and replays it **verbatim** to that same model only.
  Function-call turns from any other model get Google's placeholder signature
  `skip_thought_signature_validator` (verified live: mixed Gemini/Groq conversations work).
- Groq: `GROQ_API_KEY`, `GROQ_MODEL` (default `openai/gpt-oss-120b`; the alternative available to
  this key is `openai/gpt-oss-20b`, since `llama-3.3-70b-versatile` returns 404 for it). Measured
  limits (2026-10-11): 8,000 tokens/minute and 1,000 requests/day per gpt-oss model. One agent
  request is about 2.4k tokens of fixed prompt plus tool schemas, plus history. The Groq adapter
  strips `_`-prefixed keys before sending.
- Shared settings: `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`. Use only stable/production models,
  never preview models.
- Tool schemas are generated once from the Pydantic tool-input models (OpenAI function format).
  The Gemini adapter passes them as `parameters_json_schema`. Model arguments are always
  re-validated with Pydantic on our side; never trust a provider to enforce a schema.
- Tool calls are executed one at a time, in order (gpt-oss models do not support parallel calls).
- Only `app/agent/llm_gemini.py` and `app/agent/llm_groq.py` import provider SDKs. Agent, tools and
  domain code depend on the `LLMClient` protocol in `app/agent/llm_client.py`.
- Look up current provider docs (ai.google.dev, console.groq.com/docs) for model IDs, limits and
  SDK usage rather than relying on memory; record model changes in `CHANGE_LOG.md`.
- Exposed tools are exactly the blueprint set: `get_intake_questions`, `save_intake_answer`,
  `get_intake_status`, `check_appointment_availability`, `book_consultation`,
  `create_draft_intake`, `mark_for_human_review`, `get_handoff_status`, `finalize_intake_summary`.
  Each has a strict Pydantic input schema (`extra="forbid"`) and a structured result
  (`ok`, `data` | `error{code, message, retryable}`).
- **Never** expose arbitrary SQL, shell, filesystem, or unrestricted HTTP tools.
- `firm_id`, `intake_id` and the actor come from the session context, never from model arguments.
- The agent may only speak outcomes present in a tool result. Prompts state the no-legal-advice
  boundary, but enforcement lives in code.
- If the LLM is unavailable (timeout, rate limit, 5xx): persist current state, give a safe failure message, follow the human
  fallback; never continue a critical transaction on invented data.
- Human-approval gates: acceptance/resolution of escalations, verification of reported facts and any
  decision to take a matter are **staff-only** actions.
- API keys live only in the git-ignored `.env`. Placeholders in `.env.example`.
- Intake conversations go to Google's and Groq's servers. Only fictional data may be used.

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

# text agent (Phase 2) - real LLMs (Gemini, fallback Groq) + real DB, fictional data only
.\.venv\Scripts\python scripts/text_chat.py                    # /status /trace /quit
.\.venv\Scripts\python -u scripts/run_scripted_conversations.py --report <file.json>
#   labelled scripts: fixtures/transcripts/phase2_scripted.json; live results vary per run

# voice (Phase 4) - needs `docker compose up -d postgres livekit`
.\.venv\Scripts\python scripts/download_voice_models.py        # Piper, Kokoro, Whisper -> models/
.\.venv\Scripts\python scripts/enable_gpu.py                   # optional GPU path (re-run after pip installs)
.\.venv\Scripts\python scripts/voice_worker.py start           # real worker (Gemini/Groq chain)
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # http://127.0.0.1:8000/voice
.\.venv\Scripts\python scripts/voice_worker.py console         # talk via this PC's mic, no browser
.\.venv\Scripts\python -u scripts/voice_e2e_smoke.py [--barge-in] --report <f>  # automated WebRTC test
.\.venv\Scripts\python -u scripts/voice_browser_smoke.py --report <f>           # real Chromium + fake mic

# durable workflows (Phase 5) - needs docker compose up -d postgres temporal; UI http://127.0.0.1:8233
.\.venv\Scripts\python scripts/temporal_worker.py              # workflows + activities + outbox dispatcher
.\.venv\Scripts\python -u scripts/workflow_restart_test.py --report <f>   # kill/restart + outage test (~4 min)

# evaluation harness (Phase 6) - needs postgres + temporal (system), + livekit (audio); stop
# scripts/temporal_worker.py first (the harness runs its own worker on its own task queue)
.\.venv\Scripts\python -u scripts/run_evaluations.py --track system   # scripted model + faults (~1 min, free)
.\.venv\Scripts\python -u scripts/run_evaluations.py --track live     # real model chain; quota hit -> SKIPPED
.\.venv\Scripts\python -u scripts/run_evaluations.py --track audio    # WebRTC speech (~12 min, GPU)
.\.venv\Scripts\python -u scripts/run_evaluations.py --track all      # everything; reports -> reports/eval/
#   --only ID[,ID] --category C --limit N --resume <progress.jsonl> --pause S -v
#   audio: --audio-tts piper (TTS off the GPU: isolates STT; see Phase 6 GPU-memory finding)
#          --audio-back-to-back (no readiness gate between calls)

# review dashboard (Phase 7) - staff API + Next.js; needs postgres (+ temporal for live handoffs)
.\.venv\Scripts\python scripts/seed_demo_data.py               # sets DEMO_STAFF_PASSWORD (from .env) on the demo staff
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # run from backend/
cd frontend; npm install; npm run build; npm run start; cd ..  # http://127.0.0.1:3000 (or `npm run dev`)
#   sign in: firm northstar-legal, e.g. sana.kapoor@northstar-legal.example (coordinator)
.\.venv\Scripts\python -u scripts/dashboard_e2e_demo.py       # coordinator e2e vs real Temporal + screenshots

# hardening and demo (Phase 8)
.\.venv\Scripts\python -u scripts/record_demo.py               # repeatable demo -> docs/demo/ (DEMO.md + video)
.\.venv\Scripts\python scripts/purge_transcripts.py            # retention: delete expired transcript turns
.\.venv\Scripts\python scripts/retire_eval_firms.py [--dry-run]  # deactivate old eval-*/dash-* firms

# tracing (optional) - self-hosted Langfuse v4, UI http://localhost:3100 (login in infra/langfuse/.env)
docker compose -f infra/langfuse/docker-compose.yml --env-file infra/langfuse/.env up -d
#   then LANGFUSE_ENABLED=true + the project keys in .env; LANGFUSE_CAPTURE_CONTENT=true stores text

# checks (run pytest from backend/ so its pyproject settings apply)
cd backend; ..\.venv\Scripts\python -m pytest; cd ..
cd backend; ..\.venv\Scripts\python -m pytest -m ui; cd ..   # Playwright UI tests (built frontend, ports 3000/8000 free)
cd frontend; npx tsc --noEmit; cd ..
.\.venv\Scripts\ruff check backend scripts
.\.venv\Scripts\ruff format --check backend scripts
.\.venv\Scripts\mypy --config-file backend/pyproject.toml backend/app   # config must be explicit from the repo root
```

Notes:
- New migrations: `alembic -c backend/alembic.ini revision --autogenerate -m "..."`, then review the
  file. Autogenerate does not detect standalone sequences (see the initial migration).
- Re-run `pip install -e "backend[dev]"` after adding a new **top-level** package under `backend/`
  (the editable install maps packages that existed at install time).
- Ruff config lives in the root `ruff.toml`; `alembic` is pinned as third-party there because the
  `backend/alembic/` migrations folder would otherwise confuse import sorting.
- Integration tests need the Compose Postgres running and refuse any database not ending in `_test`.
- Automated tests never call a real LLM: they use `tests/fakes.py::ScriptedLLM` and fake SDK clients. Only the scripts above
  make live calls.

### Agent/tool layer conventions (Phase 2)
- `app/tools/executor.py::ToolExecutor` is the only way model tool calls reach the domain: JSON
  parse → strict Pydantic validation → one transaction → optional post-commit `verify` in a fresh
  session → `{"ok", "data" | "error"}`. Rejected/failed calls are audited as `tool.<name>`
  (outcome `REJECTED`/`FAILURE`) against the call session.
- Escalation rules run deterministically inside `save_intake_answer`; the model is told the outcome.
- Idempotency keys are derived in code (`book:<session>:<slot>`, `rule:<intake>:<rule>`,
  `unable:<session>`), never supplied by the model.
- Only slots returned by `check_appointment_availability` in the same conversation can be booked.
### Conversation safety layer (Phase 3)
- `domain/conversation_state.py` derives the conversation state from the database (saved fields
  with statuses, missing/declined/needs-review, escalations, booking, `next_action`). It is
  appended to the system message on **every** model request and returned by `get_intake_status`.
  Never keep authoritative state only in the model's context.
- `agent/omission_check.py`: after the model drafts a final reply, the caller's last message is
  scanned for deadline cues, phone numbers, emails, refusals, legal questions and "I'm done"
  phrases. If the matching field or step is still unsaved, the draft is discarded and the model
  gets **one** `[Automated check]` nudge per turn (audited as `agent.omission_nudged`).
  Critical-escalation recall must not depend on the model remembering to save a deadline.
- `agent/reply_guard.py`: before a reply reaches the caller, three claims are checked against the
  database: booking confirmed without a `BOOKED` appointment, lawyer/firm acceptance without an
  `ACCEPTED` escalation, "I noted your question" while `caller_questions` is unsaved. A violating
  draft is regenerated once without tools; otherwise a safe fixed reply is sent (audited as
  `agent.reply_blocked`). Negation is scoped to the claim's own clause. Both checks are
  pattern-based safety nets: extend them with tests whenever a new false claim is observed.
- Corrections keep up to 5 earlier values in `FieldEntry.history`. A `VERIFIED_BY_SYSTEM` value can
  only be changed by a staff reviewer.
- `finalize_intake` refuses an intake with nothing captured, and rejects summaries containing exact
  dates, emails or phone numbers that are not stored values. It persists `intake_records.outstanding`
  (missing/declined/needs-review keys).
- Legal questions are saved in the configured optional field `caller_questions`, never answered.
- Vague answers are saved at once as `PENDING_HUMAN_REVIEW` (with the caller's words), *then*
  clarified once. Deferring the save led the model to claim it had recorded things it had not.
### Durable workflows (Phase 5)
- Temporal (`temporalio/temporal:1.9.1` dev server, SQLite on a volume) + `temporalio` 1.34. One
  worker process (`scripts/temporal_worker.py`) runs workflows, sync activities in a thread pool,
  and the outbox dispatcher.
- **Transactional outbox:** `workflow_executions` rows are written in the *same* transaction as the
  business change: `create_escalation` → `handoff-<escalation id>`; `end_call_session` (with an
  intake) → `post-call-<session id>`. The dispatcher starts `REQUESTED` rows with deterministic IDs and
  `REJECT_DUPLICATE`; `WorkflowAlreadyStartedError` = already started. Never start these workflows
  directly from request code.
- `HandoffWorkflow`: reviewer alert (bounded retries = firm `handoff.delivery_attempts`). If it fails →
  escalation `FAILED` + `HANDOFF_FAILED_FALLBACK` alert to `handoff.fallback_role`. Otherwise it waits for
  acceptance (timer by priority, optional `status_changed` signal; the DB is authoritative), sends up to
  `max_reminders` reminders, then `UNACKNOWLEDGED_FALLBACK`.
- `BookingWorkflow` (in-call, `BOOKING_VIA_WORKFLOW=true`): one activity attempt at a time; on timeout or
  transient error it **checks the booking by idempotency key before retrying** (max 3). Business
  rejections are non-retryable. The tool waits up to `BOOKING_WAIT_SECONDS`; if not finished it returns
  `BOOKING_NOT_CONFIRMED` (never claimed as booked). If Temporal itself is unreachable it books directly
  (same key, same verification) and logs a warning.
- `PostCallWorkflow`: an `INCOMPLETE` intake is re-checked after `handoff.incomplete_follow_up_minutes`; if
  still incomplete, a system `INCOMPLETE_INTAKE` escalation is created (whose handoff goes via the outbox).
- Activities are idempotent (notifications unique per `dedupe_key`, bookings per idempotency key,
  escalation state checked before changes). Workflow code is deterministic: no I/O, no clocks, only
  activities, timers and signals.
- Fault injection (`CALENDAR_FAULT_INJECTION`, `NOTIFIER_FAULT_INJECTION`) is explicit opt-in for tests
  and demos, logged loudly at worker start.
- Tests: Temporal time-skipping environment (`tests/integration/test_workflows.py`; skip time explicitly
  with `env.sleep` - implicit skipping returned no completion event). Restart, kill and outage behaviour:
  `scripts/workflow_restart_test.py` against the real server.

### Voice layer (Phase 4)
- LiveKit Agents 1.8 owns audio, VAD (Silero), turn-taking and barge-in. `IntakeVoiceAgent`
  (`agent/voice_agent.py`) overrides `llm_node` and calls `IntakeConversation.respond()`, so voice
  uses exactly the tested text code path. A marker `ConversationOwnedLLM` must be set on the
  session: LiveKit skips replies when no LLM is configured. Its `chat()` must never be reached.
- STT: `voice/stt.py` = faster-whisper `base.en`, non-streaming per VAD segment. `STT_DEVICE=auto`:
  CUDA float16 (~0.3–0.4 s per caller turn) when available, else CPU int8 (~1.2–1.4 s).
  Segments with `no_speech_prob >= 0.6` are dropped (Whisper hallucinates "Thank you." on silence).
  An empty transcript gets "Sorry, I didn't catch that". STT writes numbers like "98,765 43,210",
  so phone parsing accepts commas.
- TTS: `TTS_ENGINE=auto` picks **Kokoro** (`voice/tts_kokoro.py`, Kokoro-82M v1.0, Apache-2.0,
  voice `af_heart`, 24 kHz) when its GPU path works, else **Piper** (`voice/tts.py`,
  `en_US-ljspeech-medium`, public-domain dataset, 22.05 kHz). Kokoro is ~2× more intelligible (Whisper
  WER 0.061 vs 0.14) but ~11× slower than Piper on CPU, so never default to Kokoro on CPU.
  LiveKit splits replies into sentences and synthesizes each one.
- GPU (optional): `scripts/enable_gpu.py` installs pinned NVIDIA CUDA 12 + 13 runtime wheels,
  `onnxruntime-gpu` and a single cuDNN 9 (cu13) that serves both libraries; `voice/gpu.py`
  registers the DLL folders. Re-run it after any `pip install` (CPU `onnxruntime` shares the
  module). `VOICE_IDLE_PROCESSES=1`: each LiveKit job process loads its own models, and several
  filled the 4 GB GPU (3.9 GB), making STT 5.8 s instead of 0.3 s. Models are warmed up in `prewarm`.
- The greeting is **not interruptible** (it carries the transcript disclosure); replies are. An
  interrupted reply is flagged on its `transcript_turns` row and in the model's history.
- Consent: `call_sessions.consent` records what was disclosed. Turns are stored only if the firm's
  `privacy.retain_transcripts` is true. Audio is never recorded. Transcript text is never logged.
- A caller hang-up closes the session: unfinished intake → `INCOMPLETE`, session `DISCONNECTED`.
- Heavy models load once per worker process (`prewarm`). Blocking work (Whisper, Piper, the
  conversation) runs in threads, never on the event loop.
- `scripts/voice_smoke_worker.py` uses a deterministic scripted model for automated audio tests
  only; the real worker is `scripts/voice_worker.py`.
- The web app (`app/main.py`) is local-demo only, bound to 127.0.0.1. The voice token endpoint is
  unauthenticated by design. The staff API (Phase 7) requires a staff session.
- The blueprint's `agent/claude_client.py` is `agent/llm_client.py` (neutral types + fallback)
  with `agent/llm_gemini.py` and `agent/llm_groq.py` adapters.

### Evaluation harness (Phase 6)
- `fixtures/scenarios/transcript_scenarios.json` (45 labelled scenarios, strict schema in
  `app/evaluation/scenario.py`) and `audio_scenarios.json` (9). Each transcript scenario has caller
  turns, expectations and the tracks it runs on. System-track scenarios also script the model:
  `say` / `tools` / `outage` steps, with `{{today+N}}` and `{{offered_slot:N}}` placeholders.
- Scripts must account for every model call the safety layer makes: an omission nudge or a guard
  block each consume one more scripted step. Running out of steps is reported as `ERROR`, never
  `PASS`.
- **system** track: `ScenarioLLM` plus real tools, DB and Temporal (its own task queue, faults
  injected per scenario, only the evaluation firm's outbox dispatched). It proves the system
  guarantees. **live** track: the configured model chain; it measures model quality. A provider
  outage there is `SKIPPED`. **audio** track: `scripts/voice_eval_worker.py` (fixed
  acknowledgement model, or `VOICE_EVAL_LLM=live`), synthesized callers, WER and latency.
- Every run uses a fresh firm `eval-<run_id>`, so the demo firm's data is untouched. Generated
  reports go to `reports/` (git-ignored); curated evidence goes to `docs/evidence/phase6/`.
- Invariants are checked on every scenario: no `VERIFIED_BY_SYSTEM` field, at most one booked
  appointment, and no unsupported claim reaching the caller (re-checked against final DB state).
- `tests/integration/test_evaluation_harness.py` runs every non-Temporal system scenario plus
  negative controls (disabled guard, disabled rules, wrong expectations) that must FAIL.

### Review dashboard and staff API (Phase 7)
- `app/api/` exposes the staff API under `/api/v1`. Route modules are thin (`routes.py`); the
  ORM-to-response mapping lives in `views.py`; `deps.py` provides the DB session and the signed-in
  staff member; `errors.py` maps domain errors to `{"error": {code, message}}`:
  401 auth, 403 permission, 404 not found, 422 validation, 409 transition/conflict.
  Read-side queries live in `domain/review_service.py`. **Every query is scoped to the signed-in
  staff member's firm.** Agent-side operations (calls, bookings, intake creation) stay
  in-process via tools; they have no HTTP endpoints.
- Auth (local demo):
  - `domain/staff_auth_service.py` hashes passwords with scrypt (stdlib) and issues opaque session
    tokens (`staff_sessions`, SHA-256 of the token stored, 8 h TTL, revoked on logout).
  - Tokens travel in the cookie `li_session` (HttpOnly, SameSite=Strict, `Secure` via
    `DASHBOARD_COOKIE_SECURE`) or as `Authorization: Bearer`.
  - Login failures are indistinguishable.
  - Demo passwords come only from `DEMO_STAFF_PASSWORD` in `.env`.
- Staff writes reuse the domain rules:
  - Accept/resolve are reviewer-only; FAILED can only be resolved.
  - Field corrections and verification use `save_intake_answer` with a staff actor, then
    `escalation_service.apply_escalation_rules`, the same function the agent's tool uses.
  - An intake can be closed only from `PENDING_REVIEW`, and only when none of its escalations is
    unresolved.
- After accept/resolve, the API sends the `status_changed` signal to `handoff-<id>`
  (`workflows/client.py::signal_handoff_status_changed`), after commit and best-effort. A finished
  workflow logs `handoff_signal_not_delivered`; this is expected and the database stays
  authoritative.
- `frontend/` (Next.js 16.3.6, React 19.3, TypeScript 5.9.3, pinned; no UI library) consists of
  client pages over `lib/api.ts`. `/api/*` is proxied to FastAPI (`next.config.ts`, `API_ORIGIN`,
  fixed at build time), so the cookie is same-origin. Times are shown in the firm's timezone.
  Caller statements are labelled unverified; handoff state shows the escalation status, the
  reviewer/fallback alerts and the workflow record exactly as stored.
- Tests: `tests/integration/test_api.py` (API on the test DB) and
  `tests/integration/test_dashboard_ui.py` (Playwright, marker `ui`, excluded by default).

### Hardening (Phase 8)
- Logging: `app/observability/logging.py` writes JSON lines; `extra={...}` keys become fields.
  The API, the Temporal worker and the voice worker call `configure_logging()`. API responses
  carry `X-Request-ID`, and the access log records method/path/status/duration only. Never log
  transcript text, contact details or secrets.
- Login throttling: `app/api/throttle.py` allows 5 failures per (firm, email) per 15 min, then
  returns 429 with `Retry-After`. It is in-process (single instance).
  `test_every_staff_route_requires_a_session` fails if a new staff route is added without auth.
- Phone validation: `IntakeQuestion.phone_digit_counts` (demo firm `[10, 11, 12]`). Wrong-length
  numbers (typical speech-to-text digit drops) are rejected, so the agent asks again.
- `TTS_ENGINE=auto` picks Kokoro only if the GPU has at least `KOKORO_MIN_GPU_MIB` (6144). Below
  that, Piper: Kokoro + Whisper per process saturated a 4 GB GPU under consecutive calls.
- Retention: `privacy.transcript_retention_days` (default 90) is applied by
  `scripts/purge_transcripts.py` (audited `transcripts.purged`, counts only).
- Evaluation/demo firms are deactivated at the end of each run (`retire_eval_firm`), and the
  outbox dispatcher only starts requests of active firms.
- Security review and remaining gaps: `docs/SECURITY.md`. Demo: `docs/demo/DEMO.md`. Overview:
  `README.md`.

### Tracing (Langfuse, Phase 8 follow-up)
- `app/observability/tracing.py` is the only module that imports the Langfuse SDK
  (`langfuse` 4.15.6, server `4.45.0` pinned in `infra/langfuse/docker-compose.yml`).
- One trace per caller turn (`intake.turn`, type agent), grouped by Langfuse session = call
  session id. Children:
  - `llm <provider:model>` generations, one per chain attempt, with tokens, latency and the
    failure reason;
  - `tool <name>`, with ok / error code;
  - `reply_guard` / `omission_check` guardrails.
- Tags: channel, plus `eval`, track and scenario id for evaluation runs.
- Off unless `LANGFUSE_ENABLED` and both keys are set; then every call is a no-op. SDK errors are
  swallowed: tracing must never break a call. Export is asynchronous (batched, plus an atexit
  flush); never flush on the call path.
- Content (caller text, model messages, tool arguments, results) is sent only with
  `LANGFUSE_CAPTURE_CONTENT=true`. Otherwise only sizes, names, statuses and token counts.
  Generation input is the messages since the model last spoke, never the whole history.
- Use `127.0.0.1`, not `localhost`, for `LANGFUSE_BASE_URL`: on Windows, localhost tried IPv6
  first and cost ~2 s per export. The export timeout is 20 s.
- The test suite forces tracing off (`tests/conftest.py`); tracing is tested with a fake client
  (`tests/unit/test_tracing.py`, `tests/integration/test_tracing_conversation.py`).
- Langfuse v4 self-hosted is "events only": read traces with `GET /api/public/v2/observations`
  (`/api/public/traces` returns 404).
- Model chain decision (2026-10-13, measured; `docs/evidence/phase8/model_comparison/`): keep
  Gemini primary. On the free tier, Groq gpt-oss-120b alone ran at a median turn of 54.8 s
  (8,000 tokens/minute versus about 4,000 input tokens per call), against 3.6 s for
  gemini-3.5-flash-lite. Only the last chain entry retries 5xx or waits out rate limits; earlier
  entries fail over immediately.

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
