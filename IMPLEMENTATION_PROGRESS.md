# Implementation Progress

Status values: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED`.
A phase is `COMPLETED` only when every acceptance criterion has recorded evidence.

**Repository state at documentation setup (2026-10-11):** empty directory, no source code, no git
repository. No prior implementation work existed, so every phase started `NOT_STARTED`.

**Last updated:** 2026-10-11 — Phases 1 and 2 completed.

**Next phase to implement:** Phase 3 — Conversational state and safety (awaiting authorization).

## Summary

| # | Phase | Status | Depends on |
|---|---|---|---|
| 1 | Domain and database foundation | COMPLETED (2026-10-11) | — |
| 2 | Text-based intake agent | COMPLETED (2026-10-11) | 1 |
| 3 | Conversational state and safety | NOT_STARTED | 2 |
| 4 | Voice loop | NOT_STARTED | 3 |
| 5 | Durable workflows | NOT_STARTED | 1, 2 (3 recommended) |
| 6 | Evaluation harness | NOT_STARTED | 3 (audio mode: 4; recovery/fault cases: 5) |
| 7 | Human-review dashboard | NOT_STARTED | 1, 5 |
| 8 | Hardening and demonstration | NOT_STARTED | 1–7 |

---

## Phase 1 — Domain and database foundation

**Status:** COMPLETED (2026-10-11)

**Objective:** Implement the fictional firm, intake questionnaire, staff, availability, appointment,
escalation and audit data models with deterministic business rules and seed data — before any LLM.

**Dependencies:** none.

**Tasks / deliverables**
- [x] Docker Compose PostgreSQL 17 service (host port 5433) with `legal_intake` and
      `legal_intake_test` databases (`docker-compose.yml`, `infra/postgres/init/`).
- [x] `backend/` Python package (`pyproject.toml`, `app/config.py`, `app/db/session.py`),
      `.env.example`, `.gitignore`, root `ruff.toml`.
- [x] SQLAlchemy models (`app/models/`) + Alembic initial migration `cad5c81e1178`: `firms`,
      `staff_members`, `availability_slots`, `intake_records`, `appointments`, `escalations`,
      `audit_events`; composite `(firm_id, id)` FKs, partial unique indexes (one `BOOKED`
      appointment per slot and per intake; one unresolved escalation per intake+rule), idempotency
      unique keys, CHECK-constrained enums, `intake_reference_seq` (INT-1001…).
- [x] Pydantic firm configuration schema (`app/schemas/firm_config.py`) with cross-reference
      validation; field provenance model (`app/schemas/intake.py`).
- [x] Pure policy engine (`app/domain/policy_engine.py`): answer normalization/validation,
      provenance rules, escalation rule evaluation, completeness.
- [x] Domain services: `firm_config_service`, `intake_service`, `appointment_service`,
      `escalation_service`, plus `actors` (authorization), `audit`, `transitions`, `errors`.
- [x] `scripts/seed_demo_data.py` (logic in `app/db/seed.py`) — idempotent: Northstar Legal,
      3 lawyers, 1 intake coordinator, 1 administrator, 120 slots over the next 10 business days.
- [x] `scripts/phase1_demo.py` — manual exit-criterion walkthrough against the real database.

**Acceptance criteria → evidence**
- [x] Draft intake created and fields saved with provenance statuses —
      `test_intake_service.py`, `test_phase1_exit_criterion.py`, demo output (INT-1001, deadline
      stored as `REPORTED_BY_CALLER`, phone `DECLINED_BY_CALLER` with null value).
- [x] Booking; same-key replay returns the same appointment; booked slot rejected; concurrent
      bookings yield exactly one appointment — `test_appointment_service.py` (incl. two threaded
      race tests); demo printed `booking replayed on retry: True | same appointment: True`; SQL
      check showed 1 appointment, slot `BOOKED`. Mutation check: removing the intake row lock makes
      the same-key race test fail, so the test detects real races.
- [x] Escalation only when the configured rule matches persisted state (`RuleNotMatched`
      otherwise); coordinator can accept/resolve; agent, system, administrator, inactive staff and
      other-firm staff cannot — `test_escalation_service.py`; demo shows `ACCEPTED`, `URGENT`.
- [x] Tenant isolation (cross-firm reads, writes, bookings, escalations all rejected) — tenant tests
      in all three service test modules; DB-level composite FKs.
- [x] Audit events for each successful state change (IDs/statuses only, no contact values) —
      verified in tests and demo output (`audit_actions` timeline).
- [x] Migrations downgrade/upgrade cleanly with no model drift — `test_migrations_and_seed.py`
      and `alembic check` → "No new upgrade operations detected."

**Validation results (2026-10-11)**
- `pytest` (from `backend/`): **111 passed** (73 unit, 38 integration against PostgreSQL 17).
- `ruff check backend scripts`: all checks passed. `ruff format --check`: 45 files already formatted.
- `mypy backend/app` (strict): no issues in 28 source files.
- `alembic upgrade head` on `legal_intake`: OK. Seed run twice: 120 slots, then 0 (idempotent).

**Known limitations (accepted for Phase 1)**
- No REST API yet (see Outstanding decision 1). Domain services are called in-process.
- Rejected/failed attempts are not audited yet; that belongs to the Phase 2 tool-executor boundary.
- No appointment cancellation or rescheduling (not required by the MVP so far).
- Seed slots are relative to the current date; re-run the seed to extend the calendar.
- An idempotency key reused at the same moment for two *different* intakes is caught only by the
  database unique constraint (raises `IntegrityError` instead of `IdempotencyConflict`).

**Blockers:** none.

---

## Phase 2 — Text-based intake agent

**Status:** COMPLETED (2026-10-11)

**Objective:** Connect the LLM (Gemini primary, Groq fallback, replacing the blueprint's Claude) to
the backend through typed tool definitions; run scripted text conversations that capture facts
through real application services.

**Dependencies:** Phase 1.

**Tasks / deliverables**
- [x] LLM layer: `LLMClient` protocol, `FallbackLLMClient` and factory (`app/agent/llm_client.py`);
      Gemini adapter via the official `google-genai` SDK with per-model thought-signature replay
      (`llm_gemini.py`); Groq adapter (`llm_groq.py`). Ordered per-model chain (`LLM_PROVIDERS`),
      quota cooldown, timeouts, bounded retries, rate-limit wait budget, `LLMUnavailable` mapping.
- [x] Tool JSON schemas generated from Pydantic input models, narrowed with firm config enums
      (field keys, category codes, rule ids); arguments re-validated; tool calls run sequentially.
- [x] The nine blueprint tools (`app/tools/*_tools.py`, registry in `app/agent/tool_registry.py`).
- [x] Tool executor (`app/tools/executor.py`): JSON + Pydantic validation, session-derived
      firm/intake/actor, one transaction per call, post-commit verification for bookings and
      escalations, structured errors, audit of rejected/failed calls, unexpected errors →
      `INTERNAL_ERROR`.
- [x] `call_sessions` table + migration `a03949963367`; `call_session_service` (start, attach
      intake, end; DISCONNECTED/FAILED → intake `INCOMPLETE`); `intake_service.finalize_intake`.
- [x] System prompt + fixed greeting (role disclosure, no legal advice, verified outcomes only).
- [x] Conversation loop (`app/agent/conversation.py`) with an LLM-outage fallback (persist state,
      `UNABLE_TO_COMPLETE` review, session `FAILED`, truthful caller message), CLI
      `scripts/text_chat.py`, live runner `scripts/run_scripted_conversations.py` with 4 labelled
      scripts in `fixtures/transcripts/phase2_scripted.json`.

**Acceptance criteria → evidence**
- [x] *Agent captures the labelled fields correctly for scripted conversations* — **two
      consecutive live runs passed 4/4** (2026-10-11, reports in `docs/evidence/phase2/`): urgent
      employment with booking, family with refused phone, ambiguous eviction deadline with pressure
      to "verify", out-of-scope patent. Every session ended `COMPLETED`; run 2 had zero tool errors
      (run 1 had one rejected `INVALID_TRANSITION`, from which the model recovered). Answers came from
      `gemini:gemini-3.5-flash-lite` (49 responses in each run) with `groq:openai/gpt-oss-20b`
      covering rate-limited calls (8 in each run). `gemini-3.8-flash` was on quota cooldown, and `gpt-oss-120b` did
      not answer. Earlier Groq-only runs, before prompt fixes: 0/1, 0/1, 2/4.- [x] *Cannot bypass business validation* — `tests/integration/test_tool_executor.py`:
      `VERIFIED_BY_SYSTEM`/`MISSING` statuses, injected `intake_id`/`firm_id`, unknown fields, invalid
      dates/codes, non-matching or invented escalation rules, un-offered slots, duplicate bookings,
      edits after finalization and cross-conversation access are all rejected and audited.
      In live runs no field was ever marked verified, and the vague eviction date stayed
      `PENDING_HUMAN_REVIEW`.
- [x] *No arbitrary SQL/shell/URL tool* — `tests/unit/test_tool_registry.py` (exactly the nine
      tools; closed schemas; no identity fields).

**Validation (2026-10-11):** `pytest` 180 passed (unit + PostgreSQL integration, no live LLM
calls); `ruff check` clean; `ruff format --check` clean; `mypy` strict: no issues in 44 files;
`alembic check`: no drift; live Gemini smoke test: multi-turn tool calling with signature replay OK.

**Known limitations (carried forward)**
- Free-tier quotas are tight and per model: `gemini-3.8-flash` allows 20 requests/day (measured).
  Groq gpt-oss allows 8k tokens/min and was exhausted for `gpt-oss-120b`. Results above come
  mainly from `gemini-3.5-flash-lite`; quality on `gemini-3.8-flash` is untested at scale.
- Latency: individual model calls took up to ~35 s (Gemini) and ~1 min per turn under Groq limits.
  This is acceptable for text testing, **not for voice**: re-measure before Phase 4.
- Behaviour observed for Phase 3: the agent sometimes offers consultation slots before being
  asked; one model reply asked several questions at once. Live checks are heuristic (scripted
  caller turns do not adapt to the agent's questions).
- Live results are nondeterministic; the 4 scripts are a smoke set, not the Phase 6 dataset.

**Blockers:** none.
## Phase 3 — Conversational state and safety

**Status:** NOT_STARTED

**Objective:** Missing-field tracking, reported-versus-verified values, interruption behavior,
escalation rules and safe handling of uncertain answers.

**Dependencies:** Phase 2.

**Tasks / deliverables**
- [ ] Structured conversation state (intake state + next action) derived from persisted data.
- [ ] Clarification flow for ambiguous answers (e.g., relative/ambiguous dates) → `PENDING_HUMAN_REVIEW`.
- [ ] Refusal handling → `DECLINED_BY_CALLER`; missing-info list in finalize.
- [ ] Legal-advice boundary handling with administrative handoff offer.
- [ ] Unsupported matter category → clarify or route for review.
- [ ] Interruption/correction handling without corrupting stored state.
- [ ] `finalize_intake_summary` validated against the intake record.

**Acceptance criteria:** the agent manages incomplete, ambiguous and refused answers without
fabricating data; every configured critical escalation in the scripted set is triggered.

**Required tests / evidence:** transcript-mode scenario tests for the blueprint §13 cases that do not
need audio or workflows; DB assertions on resulting state.

**Blockers / decisions:** none known. **Next step:** after Phase 2.

---

## Phase 4 — Voice loop

**Status:** NOT_STARTED

**Objective:** LiveKit Agents + local STT (faster-whisper), Silero VAD, local TTS (Piper; evaluate
Kokoro), browser microphone interface.

**Dependencies:** Phase 3.

**Tasks / deliverables**
- [ ] Self-hosted LiveKit server in Docker Compose; agent worker.
- [ ] STT/TTS/VAD adapters; voice session manager; interruption handling.
- [ ] `transcript_turns` table + migration; consent/disclosure metadata on call sessions.
- [ ] Minimal browser page to join a room with the microphone.
- [ ] Disconnect handling → intake `INCOMPLETE`, partial state preserved.
- [ ] Latency instrumentation (structured logs / OpenTelemetry).

**Acceptance criteria:** you can speak to the agent, hear responses, inspect the transcript and see
the corresponding intake state change.

**Required tests / evidence:** adapter unit tests; a recorded manual voice session with transcript
and DB state; measured latency numbers. Hardware/model download constraints documented.

**Blockers / decisions:** TTS voice licence; CPU/GPU capacity for faster-whisper model size;
language support (English first). **Next step:** after Phase 3.

---

## Phase 5 — Durable workflows

**Status:** NOT_STARTED

**Objective:** Temporal for finalization, booking, escalation and recovery.

**Dependencies:** Phases 1–2 (Phase 3 recommended).

**Tasks / deliverables**
- [ ] Self-hosted Temporal in Docker Compose; Temporal worker process.
- [ ] Workflows/activities for intake finalization, booking (timeout → state check before retry),
      escalation/handoff (`FAILED` + configured fallback when the queue is unavailable).
- [ ] `workflow_executions` table + migration; bounded retry policies.

**Acceptance criteria:** a worker restart does not corrupt intake records or create duplicate side
effects.

**Required tests / evidence:** Temporal test-environment workflow tests; a kill-and-restart worker
test with DB assertions (no duplicates, correct final state).

**Blockers / decisions:** none known. **Next step:** after Phase 3/4.

---

## Phase 6 — Evaluation harness

**Status:** NOT_STARTED

**Objective:** Labelled scenario dataset, transcript runner, DB assertions, audio fixtures, fault
injection and regression reports.

**Dependencies:** Phase 3 (audio mode needs Phase 4; recovery and fault scenarios need Phase 5).

**Tasks / deliverables**
- [ ] 30–50 labelled scenarios in `fixtures/scenarios/` covering blueprint §13.
- [ ] Transcript-mode runner (Mode A) and audio-mode runner (Mode B) with a simulated caller.
- [ ] Fault injection: calendar timeout, review-queue outage, disconnects, LLM (Groq)/DB failures.
- [ ] Metrics report (all blueprint §13 metrics) via `scripts/run_evaluations.py`.

**Acceptance criteria:** a single command runs the suite and reports the exact passing and failing
scenarios.

**Required tests / evidence:** the generated report with actual numbers (targets: zero false booking
confirmations, zero duplicate appointments, all critical escalations handled).

**Blockers / decisions:** live Groq runs are subject to API rate limits and usage. **Next step:** after Phases 3–5.

---

## Phase 7 — Human-review dashboard

**Status:** NOT_STARTED

**Objective:** Intake list, intake details, booking list, escalation queue, workflow state and audit
timeline for a simulated coordinator.

**Dependencies:** Phases 1 and 5.

**Tasks / deliverables**
- [ ] FastAPI surface (blueprint §12) over the domain services, with staff authentication/authorization.
- [ ] Next.js + TypeScript dashboard pages.
- [ ] Coordinator actions: accept/resolve escalations, review intakes.

**Acceptance criteria:** a simulated coordinator can review the results and complete the next action;
the dashboard shows the real handoff state.

**Required tests / evidence:** API integration tests; Playwright UI tests.

**Blockers / decisions:** authentication approach for the local demo. **Next step:** after Phase 5.

---

## Phase 8 — Hardening and demonstration

**Status:** NOT_STARTED

**Objective:** Access controls, tenant isolation, secret management, bounded retries, idempotency,
structured logging, failure recovery; repeatable demo.

**Dependencies:** Phases 1–7.

**Tasks / deliverables**
- [ ] Security and tenant-isolation review; retention/deletion policy.
- [ ] Optional Langfuse tracing.
- [ ] README: architecture diagram, setup, schema, sample conversations, scenario dataset, evaluation
      results, known limitations, walkthrough.
- [ ] Recorded demo: normal intake, urgent escalation, calendar failure, successful recovery.

**Acceptance criteria:** the system demonstrates both the happy path and predictable behavior when a
dependency fails; blueprint §18 "definition of done" items 1–11 all verified.

**Required tests / evidence:** full test + evaluation run; demo recording.

**Blockers / decisions:** none known. **Next step:** after Phase 7.

---

## Outstanding decisions

1. **REST API timing.** The blueprint lists the FastAPI surface (§12) but assigns it to no phase.
   Proposed: Phases 1–6 call domain services in-process; the HTTP API is built in Phase 7 together
   with staff authentication (so no unauthenticated write endpoints exist). Override if you want the
   API earlier.
2. **LLM provider and model.** Resolved 2026-10-11: **Gemini primary (`gemini-3.8-flash`), Groq
   fallback (`openai/gpt-oss-120b`)** (your decisions). Models to be confirmed by Phase 2 live runs.
3. **TTS engine** (Piper vs. Kokoro) and voice licence — decide in Phase 4.
4. **Supported languages** — English only until Hindi/Hinglish are tested explicitly.
5. **Firm timezone** for the fictional firm — proposed `Asia/Kolkata`.
6. **Blueprint file.** The blueprint currently exists only in the chat. Saving it in the repo (e.g.
   `docs/BLUEPRINT.md`) would let future sessions read it.
