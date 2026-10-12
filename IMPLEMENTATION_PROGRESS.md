# Implementation Progress

Status values: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED`.
A phase is `COMPLETED` only when every acceptance criterion has recorded evidence.

**Repository state at documentation setup (2026-10-11):** empty directory, no source code, no git
repository. No prior implementation work existed, so every phase started `NOT_STARTED`.

**Last updated:** 2026-10-12 — Phases 1–8 completed (DoD item 2 awaits the user's microphone test).

> Correction (Phase 4): the "mypy strict" results recorded for Phases 1–3 were produced without the
> strict config loaded (see CHANGE_LOG, Phase 4). Strict mode now runs via
> `mypy --config-file backend/pyproject.toml backend/app` and is clean.

**Next phase to implement:** none. All phases are implemented; the user's own tests are pending (see Phase 8).

## Summary

| # | Phase | Status | Depends on |
|---|---|---|---|
| 1 | Domain and database foundation | COMPLETED (2026-10-11) | — |
| 2 | Text-based intake agent | COMPLETED (2026-10-11) | 1 |
| 3 | Conversational state and safety | COMPLETED (2026-10-11) | 2 |
| 4 | Voice loop | COMPLETED (2026-10-11) | 3 |
| 5 | Durable workflows | COMPLETED (2026-10-12) | 1, 2 (3 recommended) |
| 6 | Evaluation harness | COMPLETED (2026-10-12) | 3 (audio mode: 4; recovery/fault cases: 5) |
| 7 | Human-review dashboard | COMPLETED (2026-10-12) | 1, 5 |
| 8 | Hardening and demonstration | COMPLETED (2026-10-12), DoD 2 BLOCKED on user | 1–7 |

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

**Status:** COMPLETED (2026-10-11) — live-model re-validation of the final code was cut short by
free-tier quota exhaustion (see evidence below).

**Objective:** Missing-field tracking, reported-versus-verified values, interruption behavior,
escalation rules and safe handling of uncertain answers.

**Dependencies:** Phase 2.

**Tasks / deliverables**
- [x] Structured conversation state from persisted data (`domain/conversation_state.py`): saved
      fields and statuses, missing/declined/needs-review, escalations, booking, `next_action`.
      Injected into every model request and returned by `get_intake_status`.
- [x] Clarification flow: vague answers are saved immediately as `PENDING_HUMAN_REVIEW` with the
      caller's words, then clarified once; a clarified value is re-saved and the original kept.
- [x] Refusal handling → `DECLINED_BY_CALLER`; outstanding items persisted on finalization
      (`intake_records.outstanding`, migration `1d3c29187505`).
- [x] Legal-advice boundary: the question is recorded in the configured `caller_questions` field,
      never answered; the reply guard blocks "I noted your question" when it was not saved.
- [x] Unsupported matter category → `OTHER` → `outside_intake_workflow` escalation; no slots offered.
- [x] Interruption/correction handling: re-saves keep up to 5 earlier values in field history
      (audit `corrected` flag); the agent can never overwrite a staff-verified value.
- [x] `finalize_intake_summary` validated against the record: nothing-captured is rejected; exact
      dates, emails and phone numbers in the summary must match stored values.
- [x] Deterministic safety nets beyond the plan, added after live failures:
      `agent/omission_check.py` (one nudge when a deadline, phone, email, refusal, legal question or
      "I'm done" from the caller was not acted on) and `agent/reply_guard.py` (blocks unsupported
      booking, acceptance and noted-question claims; one regeneration, then a safe reply).

**Acceptance criteria → evidence**
- [x] *Manages incomplete, ambiguous and refused answers without fabricating data* —
      `tests/integration/test_phase3_scenarios.py` (17 transcript-mode scenarios with DB
      assertions: normal enquiry, urgent deadline, ambiguous date, missing phone, refusal, legal
      question, unsupported category, interruption/correction, verified-value protection, finalize
      validation, false booking/acceptance claims, guard fallback, omission nudges), plus unit tests
      for the state, guard, omission check, corrections and summary validation.
      **Live: zero fabricated values across every Phase 3 live run.**
- [x] *Every configured critical escalation in the scripted set is triggered* — deterministic:
      rules fire on save (tests for `imminent_deadline` and `outside_intake_workflow`). Live: before
      the omission check, one run missed the urgent deadline (the model never saved it). Since the
      check, the urgent deadline escalated in every run where a model was available (2/2).

**Live runs (Gemini → Groq chain; reports in `docs/evidence/phase3/`)**

| Run | Code | Phase 2 set | Phase 3 set | Notes |
|---|---|---|---|---|
| first | Phase 3 prompt | 3/4 | 4/4 | eviction deadline not saved, yet "recorded" claimed → prompt fix |
| A | prompt fix | 3/4 | 3/4 | **urgent deadline missed** → omission check; phone refusal missed |
| B | omission check v2 | **4/4** | 3/4 | legal question "noted" but not saved → nudge + guard rule |
| C | final code | not measured (all 4 sessions FAILED on quota) | 2/4 (both scenarios a model answered passed) | quota exhausted mid-run |

One further run between B and C was rate-limited on every model and is not counted (no report kept).

**Validation (2026-10-11):** `pytest` 248 passed; `ruff check` clean; `ruff format --check` clean;
`mypy` strict: no issues in 47 files; `alembic check`: no drift.

**Known limitations**
- Free-tier quotas: all four models were exhausted on 2026-10-11. Re-run both live sets (paced with
  `--pause 60`) when quotas reset, to re-validate the final code end to end.
- The omission check and reply guard are pattern-based, English-only safety nets: they reduce but
  cannot eliminate missed extractions or false claims. Each new failure should become a pattern
  plus a regression test.
- Conversation quality issues remain (repetitive questions, unprompted slot offers, simple
  "do you handle X?" questions not answered). Measured properly in Phase 6.
- Every omission nudge costs an extra model request (latency and quota).

**Blockers:** none for the phase; live re-validation waits on quota.

---
## Phase 4 — Voice loop

**Status:** COMPLETED (2026-10-11), on automated real-audio evidence. Two items are not done yet:
a session with a human speaking, and a voice session with the live LLM (quota exhausted today).

**Objective:** LiveKit Agents + local STT (faster-whisper), Silero VAD, local TTS (Piper; evaluate
Kokoro), browser microphone interface.

**Dependencies:** Phase 3.

**Tasks / deliverables**
- [x] Self-hosted LiveKit server `v1.13.9` in Docker Compose (dev mode, ports 7880/7881/7882-udp,
      bound to 127.0.0.1); agent worker `scripts/voice_worker.py` (`dev`/`start`/`console`).
- [x] Adapters: `voice/stt.py` (faster-whisper), `voice/tts.py` (Piper), Silero VAD;
      `voice/session_manager.py` (per-process model prewarm, session wiring, events);
      `agent/voice_agent.py` (`llm_node` → `IntakeConversation`, readiness gate, interruption hook).
- [x] `transcript_turns` table + `call_sessions.consent` (migration `1602d7e7af4d`); firm
      `privacy` policy (retain transcripts + disclosure text); disclosure spoken in the greeting.
- [x] Browser page `GET /voice` + `POST /api/v1/voice/token` (`app/main.py`, local demo only).
- [x] Disconnect handling → session `DISCONNECTED`, intake `INCOMPLETE`, partial state preserved.
- [x] Latency instrumentation: per-turn `processing_ms` stored on agent turns; `stt_ms`, `tts_ms`,
      `processing_ms` logs; LiveKit `metrics_collected` logged.
- [x] `scripts/download_voice_models.py`; automated tests `scripts/voice_e2e_smoke.py`
      (`--barge-in`) and `scripts/voice_browser_smoke.py` (Playwright Chromium, fake mic).

**Acceptance criteria → evidence** (reports in `docs/evidence/phase4/`)
- [x] *Speak to the agent and hear responses* — automated caller over real WebRTC: 4/4 runs PASS
      (basic ×3, barge-in ×1). Real browser page with a WAV as the microphone: 2/2 runs PASS. The
      greeting and replies were heard and transcribed back by Whisper. The audio was synthesized,
      not a human voice.
- [x] *Inspect the transcript* — `transcript_turns` rows (agent greeting with disclosure, caller
      turn transcribed exactly, agent reply with `processing_ms`); `interrupted` flag set on a
      barged-in reply, and never on the greeting.
- [x] *See the intake state change* — the spoken "respond to a notice by tomorrow" produced the
      saved `reported_deadline` and the `imminent_deadline` escalation. A hang-up left the session
      `DISCONNECTED` and the intake `INCOMPLETE`.

**Measured latency** (scripted model, so no LLM time included):

| Measure | Values |
|---|---|
| End of caller speech → start of agent reply | 2.61–3.07 s (5 runs) |
| Agent reply after a barge-in | 2.47 s |
| Whisper `base.en` per caller utterance (~4 s of audio) | 1.18–1.44 s |
| Conversation turn (tools + DB, scripted model) | ~0.2 s |
| Piper TTS per sentence | 0.2–0.85 s |
| Greeting after joining | 1.2–3.0 s |
| Worker registration (model load) | 42–48 s |

A real LLM adds its own time to every turn: Gemini/Groq calls took 2–35 s in Phases 2–3, often
several calls per turn. **Not yet measured in voice.**

**GPU + Kokoro follow-up (2026-10-12, after disk was freed):**

| Configuration | End of speech → reply | STT per turn | TTS per sentence | Intelligibility (Whisper WER) |
|---|---|---|---|---|
| CPU Whisper int8 + Piper (Phase 4 baseline) | 2.61–3.07 s | 1.18–1.44 s | 0.2–0.85 s | 0.14 |
| **GPU Whisper float16 + Kokoro `af_heart` (now the `auto` default)** | **1.74 / 1.75 / 2.13 s** | **0.30–0.43 s** | 0.34–0.99 s | **0.061** |

- GPU voice runs: 3/3 WebRTC PASS (basic ×2, barge-in) and browser PASS
  (`docs/evidence/phase4/gpu_*.json`).
- Kokoro on CPU: real-time factor 0.54–0.60 (≈11× slower than Piper), so `TTS_ENGINE=auto` uses
  Kokoro only on GPU. The int8 Kokoro model was no faster on this CPU. Listening comparison:
  `docs/evidence/phase4/tts_comparison/*.wav`.
- Whisper `small.en` on GPU mis-transcribed the phone number ("90, 8,765"); `medium.en` was no
  faster. `base.en` kept.
- Bug found and fixed: LiveKit pre-spawned several job processes, each loading the models onto the
  GPU (3.9 / 4 GB used while idle), which made STT 5.8 s and latency 13.8 s. Fixed with
  `VOICE_IDLE_PROCESSES=1` (idle use: 793 MiB).
- Setup: `scripts/enable_gpu.py` (pinned CUDA 12 + 13 wheels, onnxruntime-gpu 1.31, one cuDNN 9).
  It must be re-run after `pip install` because CPU `onnxruntime` shares the Python module. Without
  it, the code falls back to CPU automatically (Piper, CPU Whisper).

**Hardware:** Ryzen 7 4800H (8 cores), 16 GB RAM, RTX 3050 Ti 4 GB (driver 592), ~62 GB free disk
after the GPU wheels (~5 GB) and models (~550 MB, `models/`, git-ignored).

**Decisions:** Kokoro (Apache-2.0 weights; GPU) is the default voice with Piper as the CPU
fallback; Whisper `base.en` on GPU. English only: Hindi/Hinglish untested (Kokoro has
Hindi voices graded C, a possible later option).
**Known limitations / pending**
- Voice with the real LLM chain: blocked today by free-tier quota exhaustion. Run
  `scripts/voice_worker.py start` + the web page once quotas reset, and measure turn latency.
- A human-microphone session is still to be done by you (instructions in CLAUDE.md §12).
- With live free-tier LLMs, expect multi-second to tens-of-seconds pauses per turn; a paid tier
  is likely needed for a fluid demo.
- Speech said during the greeting is discarded (the greeting is uninterruptible by design).
- The token endpoint is unauthenticated (local only, 127.0.0.1). Staff auth was added in Phase 7 (the voice token endpoint stays open by design).

**Blockers:** none for the phase.

---
## Phase 5 — Durable workflows

**Status:** COMPLETED (2026-10-12)

**Objective:** Temporal for finalization, booking, escalation and recovery.

**Dependencies:** Phases 1–2 (Phase 3 recommended).

**Tasks / deliverables**
- [x] Self-hosted Temporal (`temporalio/temporal:1.9.1`, dev server with SQLite on a volume, UI
      :8233) in Docker Compose; worker process `scripts/temporal_worker.py` (workflows + sync
      activities in a thread pool + outbox dispatcher); SDK temporalio 1.34.0.
- [x] `HandoffWorkflow` (reviewer alert with bounded retries → `FAILED` + fallback alert; timed
      reminders by priority; unacknowledged fallback; `status_changed` signal), `BookingWorkflow`
      (timeout/transient → **state check by idempotency key before retry**, max 3, rejections not
      retried; used by the in-call `book_consultation` tool), `PostCallWorkflow` (incomplete intake →
      `INCOMPLETE_INTAKE` follow-up escalation after the firm's delay).
- [x] `workflow_executions` (transactional outbox + status) and `staff_notifications` (simulated
      reviewer channel, idempotent per `dedupe_key`), migration `868d5e8c91bc` (also widens the
      escalation-reason CHECK and adds a `(firm_id, id)` key on escalations); firm `handoff` policy.
- [x] Bounded retry policies everywhere; explicit, logged fault injection
      (`CALENDAR_FAULT_INJECTION`, `NOTIFIER_FAULT_INJECTION`).

**Acceptance criteria → evidence**
- [x] *A worker restart does not corrupt intake records or create duplicate side effects* —
      `scripts/workflow_restart_test.py` against the real Temporal server, **2 runs × 3/3 PASS**
      (`docs/evidence/phase5/`):
  - **booking:** the worker committed a booking, hung (fault) and was killed. A fresh worker
    finished the workflow by state check (`replayed=True`) after 10.9 s. Exactly 1 appointment
    for the key and the intake; execution `COMPLETED`.
  - **handoff:** the worker was killed during the acknowledgement timer. After restart:
    `NEW_ESCALATION`, `REMINDER`, `UNACKNOWLEDGED_FALLBACK`, exactly once each.
  - **outbox:** with Temporal stopped, the escalation was still created and its handoff waited
    as `REQUESTED`. After Temporal restarted, the dispatcher started it (one alert).
- Time-skipping workflow tests (`tests/integration/test_workflows.py`, 11): the escalation requests
  its handoff atomically; alert → 2 reminders → fallback; stop on acceptance + signal; unavailable
  channel → `FAILED` + fallback (no reviewer alert); flaky channel → exactly one alert; booking +
  execution record; timeout-after-commit resolved by state check (1 appointment); rejection not
  retried; post-call follow-up created / not needed; lost dispatcher commit → no duplicate run.
- Tool tests: durable booking verified from the DB; `SLOT_UNAVAILABLE` kept; calendar failure and
  still-running → `BOOKING_NOT_CONFIRMED` (never claimed); unreachable Temporal → direct
  idempotent booking.

**Validation (2026-10-12):** `pytest` 291 passed; ruff + format clean; strict mypy clean (68
files); `alembic check` no drift. Voice regression after Phase 5: 2/2 PASS (latency 3.16 s and
1.81 s; stage timings normal, so the difference is end-of-speech/transport variance).

**Known limitations**
- The reviewer channel is simulated (DB inbox). A real email/SMS/Slack adapter would plug in at
  `notify_reviewers`.
- (Resolved in Phase 7: accept/resolve now send the signal.) Originally: the `status_changed` signal was not sent yet. Until then the
  handoff learns of acceptance at its next timer check.
- The Temporal dev server (SQLite) suits local use only; production would use a Temporal cluster
  with PostgreSQL.
- If Temporal is unreachable, in-call booking falls back to the direct path (logged); durable
  retries are then unavailable for that booking.

**Blockers:** none.

---
## Phase 6 — Evaluation harness

**Status:** COMPLETED (2026-10-12). The harness, dataset and reports are done and the
acceptance criterion is met. The live-model track is only partly measured (provider quota, see
below). The audio track found two real defects; they are reported, not fixed (see Known
limitations).

**Objective:** Labelled scenario dataset, transcript runner, DB assertions, audio fixtures, fault
injection and regression reports.

**Dependencies:** Phase 3 (audio mode needs Phase 4; recovery and fault scenarios need Phase 5).

**Tasks / deliverables**
- [x] **45 labelled transcript scenarios** (`fixtures/scenarios/transcript_scenarios.json`)
  covering every blueprint §13 case:
  - normal 5, urgent deadline 6 (one negative case), ambiguous date 3, missing phone 2;
  - refusal 3, legal advice 3, calendar timeout 2, duplicate booking 2, disconnect 3;
  - review-queue outage 2, unsupported category 2, interruption/correction 3;
  - adversarial model 5, LLM outage 2, DB outage 2.

  9 are labelled critical. **9 audio scenarios** (`audio_scenarios.json`).
- [x] Mode A runner (`app/evaluation/runner.py`) with two tracks:
  - **system:** a scripted model (`ScenarioLLM`) plus the real tools, DB and Temporal, with faults
    injected per scenario.
  - **live:** the real model chain.

  Mode B runner (`app/evaluation/audio_runner.py` and `scripts/voice_eval_worker.py`): synthesized
  callers over LiveKit WebRTC (Piper and 6 Kokoro voices, 1.35× speed, 15 dB noise, mid-turn
  pauses, barge-in). It measures WER (caller and agent speech), keyword recall and latency.
- [x] Fault injection: calendar timeout after commit, reviewer channel down or flaky, caller
  disconnects, LLM outage (mid-call / first turn), DB outage (on save / on booking), and an
  adversarial model (false booking/acceptance claims, forbidden status, unoffered slot,
  unjustified escalation, edit after finalize, unknown tool).
- [x] One command: `scripts/run_evaluations.py --track system|live|audio|all`, with
  `--only/--category/--limit/--resume`. It writes a JSON + Markdown report and a resumable progress
  file, and exits non-zero on any FAIL/ERROR.

**Acceptance criteria → evidence** (`docs/evidence/phase6/`)
- [x] *A single command runs the suite and reports the exact passing and failing scenarios*. See the
  per-scenario PASS/FAIL/SKIPPED/ERROR tables in each report.
- **System track** (`system_track.md`): **45/45 PASS** (two runs, the same scenario results in both; timings vary).
  - false booking confirmations reaching the caller **0**; duplicate booking rate **0.0**; critical
    escalation recall **1.0** (9/9);
  - recovery success **1.0**; handoff integrity **1.0**; unexpected internal errors **0**;
  - guard blocks 3; validation rejections 7; turn processing p50 133 ms / p95 285 ms.
  - Field accuracy 1.0 here reflects the scripted model. It is **not** a model-quality number.
- **Negative controls** (`tests/integration/test_evaluation_harness.py`): with the reply guard
  disabled, A01 FAILs on the "unsupported claim reached the caller" invariant. With escalation
  rules disabled, U03 FAILs and critical recall drops to 0.0. Wrong expectations FAIL, and a script
  mismatch is ERROR. A green run therefore detects regressions.
- **Live track** (Gemini → Groq chain): **1 PASS, 1 FAIL, 27 SKIPPED**. N01 passed. N02 FAILed
  because the model never saved the caller's city (a genuine omission; there is no deterministic
  check for jurisdiction). Every provider in the chain was then rate-limited; a resume with
  90-second pauses also stayed rate-limited, so the free-tier daily quotas were exhausted. The
  SKIPPED scenarios remain to be run (`--resume`); live model-quality metrics are not established
  yet. On the 2 completed scenarios (`live_track.md`):
  - field accuracy 0.909 (10/11); 0 false booking confirmations; 0 duplicates;
  - turn time p50 36.6 s, inflated by provider rate-limit backoff, so not a latency measure.
- **Audio track, speech recognition isolated** (`--audio-tts piper`,
  `audio_stt_isolated_piper_tts.md`): 5/9 PASS.
  - caller WER mean **0.079** (0.00–0.29); agent-speech WER **0.016**; keyword recall **0.839**;
  - response latency p50 **1.35 s** / p95 **2.92 s**; barge-in **1/1**; 15 dB noise passed (WER 0.05).
  - The 4 FAILs are genuine recognition errors (see limitations).
- **Audio track, production voice config** (Kokoro TTS on the GPU): **3/9 PASS** in both
  readiness-gated runs. The early calls match the isolated run. From call 5 (run 1) or call 4
  (run 2) every scenario fails, including voices that pass in isolation.
  - The VRAM trace (`vram_trace_kokoro_gpu_5_calls.txt`) shows 2.0 → 3.3 → **3.95 GB of 4 GB**
    with a constant process count.
  - Per-call timings (`audio_production_config_timings.txt`): Kokoro takes 0.33–0.75 s per sentence
    in calls 1–3 and 1.9–7.1 s from call 4. Only one caller utterance per call reaches STT instead
    of two.
  - In one earlier run, CUDA out-of-memory errors appeared in both STT and TTS. That worker log
    was overwritten before per-run logs were added, so only the report remains
    (`audio_production_config_kokoro_gpu_run1.json`, AU09 timeout).

**Validation (2026-10-12):** `pytest` 372 passed (81 new); ruff + format clean; strict mypy clean
(77 files); `alembic check` no drift.

**Known limitations / findings for later phases**
- **GPU memory (defect, Phase 4 runtime):** each voice process holds about 2 GB (Whisper on
  CUDA 12 plus Kokoro on CUDA 13). LiveKit keeps the active process plus one prewarmed spare, which
  saturates a 4 GB GPU by the 3rd–4th consecutive call. Effects:
  - Kokoro then slows 5–10×, so the agent's replies contain multi-second silences.
  - Callers talk into those silences, and turns are lost or merged.
  - In one run, CUDA ran out of memory.

  With TTS off the GPU (`--audio-tts piper`), the same 9 calls show no such degradation. Options
  for Phase 8: share one model set across jobs (thread executor), cap the onnxruntime arena, or
  use Piper on ≤4 GB GPUs. The single-call Phase 4 tests could not reveal this.
- **Phone numbers by voice (risk):** Whisper base.en dropped or merged digits in 3/9 calls (e.g.
  "90080 70060" → "90,000 AD7060"). A wrong callback number could be saved. Options for Phase 8:
  read-back confirmation of digits, a larger STT model, and digit-aware decoding.
- A mid-turn pause stores the first fragment twice (LiveKit re-commits the user turn after an
  interrupted reply). Names are sometimes misspelled (Rohen Dass, Ned Bansal).
- The live track needs provider quota; quota exhaustion is SKIPPED, never PASS.
- Kokoro's Hindi-trained voice speaking English is an accent proxy, not real accented speech.

**Blockers:** none for the phase. Live-track coverage is pending provider quota.

---

## Phase 7 — Human-review dashboard

**Status:** COMPLETED (2026-10-12)

**Objective:** Intake list, intake details, booking list, escalation queue, workflow state and audit
timeline for a simulated coordinator.

**Dependencies:** Phases 1 and 5.

**Tasks / deliverables**
- [x] **Staff API** (`app/api/`, under `/api/v1`), thin routes over domain services. Every query
  is scoped to the signed-in staff member's firm.
  - Auth: `POST auth/login|logout`, `GET auth/me`.
  - Intakes: `GET intakes[?status]`, `GET intakes/{id}`, `PATCH intakes/{id}/fields/{key}`,
    `POST intakes/{id}/close`.
  - Escalations: `GET escalations[?status]`, `GET escalations/{id}`, `POST .../accept`,
    `POST .../resolve`.
  - Other reads and inbox: `GET appointments[?upcoming]`, `GET appointments/{id}`,
    `GET workflows`, `GET notifications`, `POST notifications/{id}/read`.

  Agent-side blueprint endpoints (calls, intake creation, bookings) stay in-process via the
  validated tools (decision 1 below).
- [x] **Authentication (local-demo decision):**
  - staff email + password (scrypt, stdlib);
  - opaque session tokens hashed in `staff_sessions` (migration `4b103e7be80c`, which also adds
    `staff_members.password_hash`); 8 h TTL; revoked on logout;
  - an HttpOnly, SameSite=Strict cookie, or a Bearer header;
  - login failures that cannot be told apart;
  - passwords only from `DEMO_STAFF_PASSWORD` in `.env`.
- [x] **Next.js + TypeScript dashboard** (`frontend/`; Next 16.3.6, React 19.3.0, TS 5.9.3, pinned):
  - pages: sign-in, overview (counts + reviewer inbox), escalation queue (status filters),
    intakes list, intake detail, bookings, workflows;
  - `/api` proxied to FastAPI (same-origin cookie).
- [x] **Coordinator actions:**
  - accept / resolve escalations (reviewers only; FAILED can only be resolved);
  - verify or correct fields: provenance kept, correction history shown, firm rules re-applied
    through the same `apply_escalation_rules` the agent uses;
  - close an intake: only from `PENDING_REVIEW` with no unresolved escalation (new domain rule);
  - mark alerts read.
- [x] Accept/resolve send the Phase 5 `status_changed` signal to the handoff workflow, after commit
  and best-effort.

**Acceptance criteria → evidence**
- [x] *A simulated coordinator can review the results and complete the next action; the dashboard
  shows the real handoff state.* `scripts/dashboard_e2e_demo.py` uses the real conversation
  pipeline (scripted model), real Temporal, the real API and a built dashboard in Chromium:
  **PASS, 9/9 checks** (`docs/evidence/phase7/dashboard_e2e.json`, 6 screenshots).
  - The failed handoff (reviewer channel down) is shown as FAILED, with the fallback alert and no
    Accept button.
  - The urgent handoff is shown as delivered and running.
  - The overview counts match the database.
  - **Accept completed the waiting HandoffWorkflow (result ACCEPTED) in 0.72 s** through the
    signal; its timer would otherwise run 15 minutes. The dashboard then shows
    `COMPLETED (ACCEPTED)`.
  - The booking is shown; a field is verified; both escalations are resolved; the intake is
    closed.
  - The timeline holds agent and staff actions in commit order.
- **API integration tests** (`tests/integration/test_api.py`, 14): login and cookie flags;
  indistinguishable failures; logout revocation; bearer + expiry; tenant isolation (404);
  queue/accept/resolve with audit + signal; non-reviewer 403; FAILED not acceptable; verify and
  correct with rules re-applied; close guarded; lists; UNABLE_TO_COMPLETE shown.
- **Playwright UI tests** (`tests/integration/test_dashboard_ui.py`, 5, marker `ui`): **5/5 PASS
  in two runs**. They cover the sign-in error and redirect, accept then resolve (the note is
  required), verify / close-guard / history, the administrator read-only view, and the
  failed-handoff view.

**Validation (2026-10-12):**
- `pytest` 386 passed (default run) + 5 UI tests passed.
- Phase 6 system track still 45/45 after the rule-application refactor.
- ruff + format clean; strict mypy clean (85 files); `alembic check` no drift.
- `tsc --noEmit` clean; `next build` clean.

**Known limitations**
- Local-demo authentication:
  - no rate limiting or lockout on login;
  - no password reset or staff administration UI;
  - CSRF protection relies on SameSite=Strict plus JSON bodies;
  - the cookie is not `Secure` over local HTTP.
- No pagination (lists are capped at 200 rows). The dashboard does not auto-refresh; reload to see
  workflow progress.
- Staff names are not resolved in the audit timeline (it shows actor ids).
- UI tests need ports 3000/8000 free and a built frontend. `API_ORIGIN` is fixed when the
  frontend is built.
- Evaluation runs leave REQUESTED outbox rows for their `eval-*` firms; a regular
  `temporal_worker.py` would dispatch them. This is harmless to the demo firm but noisy (Phase 8).

**Blockers:** none.

---

## Phase 8 — Hardening and demonstration

**Status:** COMPLETED (2026-10-12), except definition-of-done item 2, which is **BLOCKED on the
user**: it needs someone speaking into a real microphone (deferred by the user). Every other
deliverable and DoD item is verified below.

**Objective:** Access controls, tenant isolation, secret management, bounded retries, idempotency,
structured logging, failure recovery; repeatable demo.

**Dependencies:** Phases 1–7.

**Tasks / deliverables**
- [x] **Fixes for the Phase 6 findings**
  - Phone numbers: `IntakeQuestion.phone_digit_counts` (demo firm: 10/11/12 digits). Numbers of
    the wrong length are rejected with an "ask again" message instead of being saved.
  - GPU memory: `TTS_ENGINE=auto` uses Kokoro only on GPUs with ≥ `KOKORO_MIN_GPU_MIB` (6144);
    on this 4 GB laptop it now picks Piper automatically.
- [x] **Access controls, from the Phase 7 gaps**
  - Login throttling: 5 failures per account per 15 min → 429 with `Retry-After`.
  - A guard test fails if any new staff route lacks authentication; `/openapi.json` is disabled.
  - Staff names in the audit timeline.
  - Paging (`limit`/`offset`) on all lists; the overview and queue auto-refresh every 15 s.
- [x] **Security and tenant-isolation review:** `docs/SECURITY.md` covers the data inventory,
  the controls with their tests, and the gaps before real use. API sweep: other-firm records are
  404 on all 8 id routes, absent from all 5 lists, and unchanged.
- [x] **Retention/deletion policy:** `privacy.transcript_retention_days` (default 90),
  `purge_expired_turns` (audited, counts only), and `scripts/purge_transcripts.py`.
- [x] **Structured logging:** JSON lines that keep `extra` fields, `X-Request-ID` correlation,
  and an access log without bodies or values. Used by the API (at server start), the Temporal
  worker and the voice worker.
- [x] **Evaluation hygiene:** runs deactivate their firm, and the dispatcher ignores inactive
  firms. 13 older eval/demo firms with **74 undispatched workflow requests** were retired
  (`scripts/retire_eval_firms.py`).
- [x] **README:** architecture and ER diagrams, setup, schema, sample conversations, dataset,
  results, walkthrough, limitations.
- [x] **Recorded demo:** `scripts/record_demo.py` → `docs/demo/DEMO.md` plus
  `dashboard_walkthrough.webm`. It covers a normal intake, an urgent escalation, a calendar
  failure with recovery, and the coordinator review.
- [ ] Optional Langfuse tracing: not added (documented in `docs/SECURITY.md`).

**Acceptance criteria → evidence** (`docs/evidence/phase8/`, `docs/demo/`)
- [x] *Happy path and predictable behaviour when a dependency fails.* `record_demo.py`:
  **PASS 7/7**. Temporal's own history shows `book_slot` FAILED after commit, then
  `find_booking`, then exactly one appointment. Accept completed the waiting handoff workflow.
  The Phase 6 fault scenarios (calendar, reviewer channel, LLM, DB, disconnects) still pass.

Definition of done (blueprint §18):

| # | Item | Evidence |
|---|---|---|
| 1 | Start the app, open the voice interface | Phase 4 `voice_browser_smoke.py` (real Chromium on `/voice`); README setup |
| 2 | Speak through **your** microphone | **BLOCKED: needs the user.** Automated equivalent: Chromium fake-mic WebRTC (Phase 4) and the audio track (9 calls) |
| 3 | Describe a fictional enquiry | Audio track (synthesized callers), live track (29 enquiries) |
| 4 | Agent captures structured information | DEMO.md tool calls + DB state; dashboard fields |
| 5 | Statuses on reported facts | DEMO.md fields (`REPORTED_BY_CALLER`), dashboard provenance labels, UI tests |
| 6 | Configured escalation in the review queue | Demo scene 2 + dashboard video/screenshots |
| 7 | Consultation booked, verified in PostgreSQL | Demo scene 1 DB state (1 BOOKED appointment, re-read before confirming) |
| 8 | Incomplete answer / disconnect without corruption | X01/X02 (system + live PASS), P02, Phase 4 hang-up test |
| 9 | Calendar timeout and safe recovery | Demo scene 3 (Temporal history) + C01/C02 |
| 10 | Run the evaluation suite and inspect it | `run_evaluations.py`, reports below |
| 11 | Complete action history in the dashboard | Intake detail timeline (demo screenshots, UI tests) |

**Full evaluation run (final code, 2026-10-12)**
- **System:** 45/45 PASS. False booking confirmations 0; duplicates 0.0; critical recall 1.0
  (9/9); recovery 1.0; handoff integrity 1.0.
- **Live** (Gemini → Groq, quotas reset): **26 PASS / 3 FAIL / 0 SKIPPED** of 29. Field accuracy
  0.972; critical recall 1.0 (6/6); 0 false booking confirmations; 0 duplicates; 10 omission
  nudges. Turn p50 6.3 s is inflated by rate-limit backoff. The FAILs:
  - N02: the city was not saved.
  - R03: the refused matter description was saved as REPORTED text instead of
    DECLINED_BY_CALLER.
  - L02: the legal question was saved as PENDING_HUMAN_REVIEW with no value. The model correctly
    refused to advise; its phrase "can't tell you whether you have a case" also tripped a
    forbidden-phrase substring, a harness false positive since fixed in the dataset (disclosed in
    CHANGE_LOG).
- **Audio, production config** (auto → Piper on 4 GB): **5/9 PASS**, no GPU errors, no
  degradation across 9 consecutive calls. Before, it was 3/9, with failures from call 4 or 5.
  Caller WER 0.084; agent-speech WER 0.033; keyword recall 0.839; latency p50 2.42 s / p95
  2.88 s; barge-in 1/1. The 4 FAILs are speech-recognition misses on names and digits. Of the 3
  misheard phone numbers, the new length check rejects 2; the third (`9800110099`) has a valid
  length.
- Tests: `pytest` 411 passed + 5 Playwright UI passed; ruff/format, strict mypy (89 files),
  `alembic check`, `tsc` clean.

**Known limitations** (full list: `docs/SECURITY.md` "Gaps", README)
- DoD 2 (real microphone) and a live-model voice call still need the user.
- Whisper `base.en` can mishear a phone number with a plausible length. Staff verify contact
  details; a read-back confirmation by the agent would need live-model testing.
- Local-demo security: unauthenticated voice token endpoint, no TLS, dev-mode Temporal/LiveKit,
  in-process login throttle, app connects as DB owner.
- No Langfuse tracing; English only.

**Blockers:** DoD item 2 needs the user's microphone (deferred by the user).

---

## Outstanding decisions

1. **REST API timing.** Resolved in Phase 7: the staff (review-side) API was built together with
   staff authentication. The agent's operations stay in-process via validated tools, so no
   unauthenticated write endpoint exists.
2. **LLM provider and model.** Resolved 2026-10-11: **Gemini primary (`gemini-3.8-flash`), Groq
   fallback (`openai/gpt-oss-120b`)** (your decisions). Models to be confirmed by Phase 2 live runs.
3. **TTS engine** (Piper vs. Kokoro) and voice licence — decide in Phase 4.
4. **Supported languages** — English only until Hindi/Hinglish are tested explicitly.
5. **Firm timezone** for the fictional firm — proposed `Asia/Kolkata`.
6. **Blueprint file.** The blueprint currently exists only in the chat. Saving it in the repo (e.g.
   `docs/BLUEPRINT.md`) would let future sessions read it.
