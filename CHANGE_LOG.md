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

---

## 2026-10-11 — Version control setup

- Initialized git (branch `main`), remote `origin` = https://github.com/kshitij189/Legal-Intake-Agent.git.
- Phase 1 committed as `eee7ca6` and pushed. Author and committer: `kshitij189`; no co-author trailers.
- Added CLAUDE.md §15 "Git and authorship": commit and push after every phase; `kshitij189` is the
  sole author; no AI attribution in commits, PRs or files.

---

## 2026-10-11 — LLM provider switched from Anthropic (Claude) to Groq

**Reason:** the user no longer has an active Anthropic API key and will use a Groq API key. This is
a deliberate, user-approved deviation from the blueprint's "Claude API via the Anthropic SDK".

**Changes**
- `backend/app/config.py`: added `groq_api_key` (`SecretStr`, never printed), `groq_model` (default
  `openai/gpt-oss-120b`), `llm_timeout_seconds` (20) and `llm_max_retries` (2).
- `.env.example` (and the local, git-ignored `.env`): `ANTHROPIC_API_KEY` replaced by
  `GROQ_API_KEY`, `GROQ_MODEL`, `LLM_TIMEOUT_SECONDS` and `LLM_MAX_RETRIES`.
- CLAUDE.md §4/§5/§8 and IMPLEMENTATION_PROGRESS.md (Phase 2, Phase 6, Outstanding decision 2) now
  name Groq as the provider.
- Added `backend/tests/unit/test_config.py`.

**Decisions**
- Default model `openai/gpt-oss-120b`: a Groq **production** model with tool calling and a 131k
  context window (checked against console.groq.com/docs/models and /docs/tool-use on 2026-10-11).
  Fallback: `llama-3.3-70b-versatile`. Preview models are excluded because Groq may withdraw them at
  short notice.
- The gpt-oss models do not support parallel tool calls, so the agent runs tool calls one at a time,
  in order.
- The provider sits behind an `LLMClient` adapter so the rest of the code never imports the Groq SDK,
  and Anthropic can be restored later.
- The `groq` SDK is not installed yet. It will be added in Phase 2, when the code first uses it.

**Tests executed (actual results):** `pytest` 113 passed (adds 2 config tests); ruff check passed;
ruff format: 46 files unchanged; mypy strict: no issues in 28 files. No live Groq call was made: no
real key is configured yet, and no code calls the LLM before Phase 2.

---

## 2026-10-11 — Phase 2 (part 1): text-based intake agent

**Status at the time of this entry:** code and offline tests complete; live acceptance pending.
Completed in the next entry.

**Added**
- `call_sessions` model + migration `a03949963367`; `domain/call_session_service.py`;
  `intake_service.finalize_intake`; `list_escalations(intake_id=...)`;
  `appointment_service.get_booked_appointment_for_intake`; `Action.SESSION_MANAGE`.
- `app/tools/`: `schemas.py` (strict inputs; agent statuses exclude `VERIFIED_BY_SYSTEM`/`MISSING`),
  `context.py`, `intake_tools.py`, `calendar_tools.py`, `escalation_tools.py`, `executor.py`.
- `app/agent/`: `tool_registry.py` (nine tools, config-narrowed JSON schemas), `prompts.py`,
  `conversation.py` (loop, LLM-outage fallback, trace), `llm_client.py` (neutral types,
  `FallbackLLMClient`, factory), `llm_gemini.py`, `llm_groq.py`.
- Scripts: `scripts/text_chat.py`, `scripts/run_scripted_conversations.py`; fixtures
  `fixtures/transcripts/phase2_scripted.json` (4 labelled scenarios).
- Tests: `tests/fakes.py` (ScriptedLLM), unit tests for the tool registry, prompts, Groq adapter,
  fallback chain, Gemini conversion/errors; integration tests for the tool executor and the
  conversation loop.
- Dependencies: `groq` 0.37.1, `google-genai` 1.75.0.

**Decisions**
- Escalation rules are applied deterministically inside `save_intake_answer`; the model only
  reports the outcome. Critical-escalation recall does not depend on the model choosing to escalate.
- Idempotency keys are derived in code; only slots offered in the same conversation can be booked;
  bookings and escalations are re-read after commit before success is reported.
- An LLM outage keeps partial state, queues an `UNABLE_TO_COMPLETE` review (verified), ends the
  session `FAILED` and tells the caller only what was persisted.
- **Provider change (user decision):** Gemini primary, Groq fallback. Gemini uses the official
  native SDK rather than the OpenAI-compatible layer, which Google labels beta. Community reports
  show it dropping thought signatures in multi-turn tool calling. Gemini's own content is replayed
  verbatim; Groq-produced function-call turns get Google's placeholder signature. Only the last
  provider in the chain waits out rate limits.
- Groq `llama-3.3-70b-versatile` returned 404 `model_not_found` for this key, so the documented
  alternative is now `openai/gpt-oss-20b`.

**Prompt iterations driven by live runs** (Groq `openai/gpt-oss-120b`)
- Run 1: the model never saved the volunteered matter details, and recorded `matter_category` as
  `DECLINED_BY_CALLER` although the caller never refused. Fix: save volunteered facts; infer the
  category when clear; `DECLINED_BY_CALLER` only on an explicit refusal.
- Run 2: correct booking (confirmed only after `verified=true`) and finalization; missed the email
  refusal; read raw slot UUIDs to the caller and used markdown. Fix: explicit-refusal rule for
  optional fields; no internal IDs; plain sentences.
- Run 3: **2/4 scenarios passed** (family/refused-phone and out-of-scope/OTHER). Failures: missed
  `jurisdiction`; missed `preferred_contact_method`; did not finalize when the caller said "that's
  all". Safety held: the vague eviction date was stored as `PENDING_HUMAN_REVIEW`, the caller's
  request to mark it verified was refused, and legal advice was declined. Fix: per-turn check
  against every field; finalize immediately when the caller is done; one question per reply.
- Runs 4 and 5: every scenario got 429 rate-limited (session `FAILED`,
  `llm_unavailable:rate_limited`). These measure nothing about behavior, but they confirmed the
  outage fallback on a real outage: partial intakes were kept as `INCOMPLETE` and
  `UNABLE_TO_COMPLETE` reviews were queued.

**Measured provider facts:** Groq gpt-oss-120b/20b for this key: 8,000 tokens/minute, 1,000
requests/day (response headers). The system prompt is 4,725 characters and the tool schemas
4,965 characters, roughly 2.4k tokens per request before history. One scenario took 2–5 minutes
under these limits.

**Tests executed (actual results):** `pytest` 177 passed, 0 failed; `ruff check` clean;
`ruff format --check` clean; `mypy` strict: no issues in 44 files; `alembic upgrade head` +
`alembic check` on the main DB: no drift.

**Known issues:** live Gemini path unverified (no key yet); Phase 2 field-capture acceptance not
yet met; per-turn latency under the Groq free tier is far too high for voice (Phase 4 concern).

---

## 2026-10-11 — Phase 2 (part 2): Gemini live verification, model chain, phase completed

**Live Gemini smoke test** (`gemini-3.8-flash`, before quota exhaustion): the model ID exists for the
key, our JSON tool schemas were accepted via `parameters_json_schema`, and a 3-turn tool-calling
round trip with verbatim thought-signature replay succeeded. Gemini emitted parallel tool calls,
which the executor runs sequentially. One call took 34.9 s.

**Problem found:** first full Gemini run, 1/4 passed and every session ended `FAILED`
(`gemini:rate_limited; groq:rate_limited`). The captured 429 showed quota
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, **limit 20 requests/day** for
`gemini-3.8-flash`, with `retryDelay` 34,487 s. `gemini-3.5-flash-lite` still answered (quotas are
per model). Google does not publish free-tier numbers; they are only visible in AI Studio.

**Changes**
- `LLM_PROVIDERS` is now an ordered **per-model chain** of `provider[:model]` entries, default
  `gemini:gemini-3.8-flash, gemini:gemini-3.5-flash-lite, groq:openai/gpt-oss-120b,
  groq:openai/gpt-oss-20b`. Gemini stays primary and Groq the fallback, as requested.
- `FallbackLLMClient` **quota cooldown**: an entry whose 429 retry delay exceeds 60 s is skipped
  until the stated reset, instead of being retried on every call. `LLMUnavailable` carries
  `retry_after`.
- Adapters are named `provider:model`. Gemini provider state is keyed per model and replayed only
  to the same model (signatures are model-specific); all other turns get the placeholder signature.
- Live-run reports saved as evidence: `docs/evidence/phase2/live_run_1.json`, `live_run_2.json`
  (fictional data; checked to contain no API keys).
- Tests: per-model factory/chain, bare-entry defaults, cooldown, short-429 non-cooldown.

**Live acceptance results (actual):**
- Run 1: **4/4 passed**, all sessions `COMPLETED`, one rejected tool call (`INVALID_TRANSITION`,
  model recovered).
- Run 2: **4/4 passed**, all sessions `COMPLETED`, zero tool errors.
- Each run had 49 responses from `gemini:gemini-3.5-flash-lite` and 8 from
  `groq:openai/gpt-oss-20b`. `gemini-3.8-flash` was on cooldown; `gpt-oss-120b` never answered.
- Mixed Gemini→Groq→Gemini conversations worked, which confirms live that the placeholder thought
  signature is accepted.
- Safety observations: no field was marked verified; the vague eviction date stayed
  `PENDING_HUMAN_REVIEW`; the request to "mark it verified" was refused; legal advice was
  declined; bookings were confirmed only after `verified=true`; escalations came only from
  configured rules.

**Tests executed (actual):** `pytest` 180 passed; `ruff check` clean; `ruff format --check` clean;
`mypy` strict: no issues in 44 files.

**Known limitations:** see Phase 2 → "Known limitations" in `IMPLEMENTATION_PROGRESS.md` (tight
free-tier quotas, voice-unfriendly latency, proactive slot offers, heuristic live checks).

**Next:** Phase 3 — Conversational state and safety (awaiting authorization).

---

## 2026-10-11 — Phase 3: conversational state and safety

**Added**
- `domain/conversation_state.py`: state derived from the DB (fields/statuses, missing, declined,
  needs-review, escalations, booking, `next_action`). Appended to the system message on every model
  request; `get_intake_status` returns `next_action`.
- `FieldEntry.history` (≤5 earlier values) via `policy_engine.merge_with_previous`; audit `corrected`
  flag; staff-verified values protected from agent/system overwrites.
- `finalize_intake` validation (nothing captured → rejected; summary dates, emails and phones must
  match stored values via `policy_engine.unsupported_summary_facts`); `intake_records.outstanding`
  column (migration `1d3c29187505`).
- Demo firm config: optional `caller_questions` field for legal questions (appended, so existing
  question indices are unchanged); main DB reseeded.
- `agent/reply_guard.py`: blocks unsupported booking, acceptance and "question noted" claims;
  regenerates once with no tools, then falls back to a safe fixed reply; audited
  `agent.reply_blocked`.
- `agent/omission_check.py`: one `[Automated check]` nudge per turn when the caller's message has a
  deadline cue, phone, email, refusal (naming the refused fields), legal question or "I'm done"
  that the stored state does not reflect; audited `agent.omission_nudged`.
- Prompt rules: save vague answers immediately as `PENDING_HUMAN_REVIEW`, then clarify once; never
  claim something was recorded unless the save succeeded; corrections; interruptions; offer slots
  only when asked or after the required fields; trust the injected state.
- Runner: `--scenarios`, `in`/`not_null`/`history_at_least` checks, `replies_must_not_contain`,
  guard-based claim check, reports of outstanding items, corrections and guard blocks. New fixture
  `fixtures/transcripts/phase3_scripted.json` (correction, legal advice, vague→clarified date,
  refuses everything).
- Tests: `test_phase3_scenarios.py` (17 transcript-mode scenarios with DB assertions), unit tests for
  reply guard, omission check, conversation state, corrections and summary validation.

**Bugs found by tests and live runs, then fixed**
- Guard false positive: the guard's own fallback ("nobody has accepted it yet") matched the
  acceptance pattern → "nobody/no one/none" added as negations.
- Guard false negative: "Don't worry, the lawyer has accepted your case" passed, because "n't" in
  "Don't" negated the whole sentence → negation now scoped to the claim's own clause.
- Omission check: ISO dates were also detected as phone numbers → dates stripped before the phone
  scan.
- Prompt regression: "clarify before saving" made the model claim it had recorded a vague eviction
  date that was never saved → save-first-then-clarify rule.
- Live: the urgent deadline went unsaved in one run (no escalation) → omission check. The legal
  question was "noted" but not saved → legal-question cue plus a guard rule.

**Live results (actual):** see the Phase 3 table in `IMPLEMENTATION_PROGRESS.md` and the reports in
`docs/evidence/phase3/`. Best full run on the omission-check code: Phase 2 set 4/4, Phase 3 set 3/4.
On the final code, only 2 scenarios could be measured before all four free-tier models were exhausted
(`gemini-3.5-flash-lite` reached its daily quota); both passed. No fabricated value in any run.

**Tests executed (actual):** `pytest` 248 passed; `ruff check` clean; `ruff format --check` clean;
`mypy` strict: no issues in 47 files; `alembic upgrade head` + `alembic check` on the main DB: no
drift.

**Known limitations:** pattern-based, English-only safety nets; nudges cost extra requests; free-tier
quotas block repeated live evaluation; conversation-quality issues are left for Phase 6 measurement.

**Next:** Phase 4 — Voice loop (awaiting authorization). Before it, re-run both live sets once quotas
reset, and re-check latency.

---

## 2026-10-11 — Phase 4: voice loop

**Added**
- Docker Compose `livekit` service (`livekit/livekit-server:v1.13.9`, dev mode, 127.0.0.1-bound
  ports 7880/7881/7882-udp; ports confirmed from the server's own startup log).
- `voice/audio.py`, `voice/stt.py` (`WhisperSTT`: faster-whisper, CPU int8, non-streaming per VAD
  segment, silence-hallucination filter), `voice/tts.py` (`PiperTTS`), `voice/session_manager.py`
  (prewarm, session wiring: interruption, close, metrics, outage hang-up), `agent/voice_agent.py`
  (`IntakeVoiceAgent`, `ConversationOwnedLLM`).
- `transcript_turns` table and `call_sessions.consent` (migration `1602d7e7af4d`, `{}` server
  default for existing rows); `domain/transcript_service.py`; firm `privacy` policy; the greeting
  speaks the disclosure; `IntakeConversation` records turns with `processing_ms` (text and voice).
- `app/main.py` (FastAPI: `/voice` page, `/api/v1/voice/token`) and `app/web/voice.html`
  (livekit-client 2.22.3 from jsDelivr, verified URL).
- Scripts: `voice_worker.py`, `voice_smoke_worker.py` (deterministic model for audio tests only),
  `voice_e2e_smoke.py` (`--barge-in`), `voice_browser_smoke.py` (Playwright), `download_voice_models.py`.
- Tests: `test_voice_adapters.py` (fake Whisper/Piper through real LiveKit stream plumbing),
  `test_voice_agent.py` (LiveKit text-mode session harness → conversation → DB),
  `test_transcripts.py`.
- Dependencies: livekit-agents 1.8.6, livekit-plugins-silero 1.8.6, livekit-api 1.2.1,
  faster-whisper 1.2.1 (ctranslate2 4.8.2), piper-tts 1.8.0, fastapi 0.143.0, uvicorn 0.54.0; dev:
  playwright 1.63.0 (+ Chromium). All installed with Windows/Python 3.13 wheels.

**Decisions (with measurements)**
- STT `base.en` CPU int8: 1.8 s for a 14 s utterance vs `small.en` 4.7 s, same transcript. GPU
  skipped (≈2 GB CUDA libraries, 9.8 GB free disk).
- TTS Piper `en_US-ljspeech-medium`: model card "License: public domain"; warm RTF 0.07. Kokoro
  not evaluated (disk; Piper adequate) — revisit in Phase 8.
- LiveKit skips replies when a session has no LLM (read in `agent_activity.py`), so a marker LLM is
  set and `llm_node` delegates to the existing conversation.
- Greeting not interruptible (disclosure integrity); replies interruptible.

**Bugs found and fixed**
- Whisper writes spoken numbers with commas ("98,765 43,210"): phone validation, omission check
  and summary fact check now accept commas (regression tests added).
- Race: a caller turn could reach `llm_node` before `on_enter` had started the conversation →
  readiness event.
- **Correction to earlier entries:** `mypy backend/app` run from the repository root did not load
  `backend/pyproject.toml`, so the reported "mypy strict" results for Phases 1–3 were actually
  mypy defaults. With `--config-file backend/pyproject.toml`, strict mode found 3 unused
  `type: ignore`s and 3 missing generic parameters (all fixed); it is now clean on 56 files.
  CLAUDE.md's command is corrected.
- E2E check too strict: Whisper heard "Northster", so the greeting check now looks for role and
  boundary wording instead of the proper noun (the first run reported FAIL only on that check).

**Tests and validations executed (actual)**
- `pytest` 265 passed; `ruff check` clean; `ruff format --check` clean;
  `mypy --config-file backend/pyproject.toml backend/app` (strict): no issues in 56 files;
  `alembic upgrade head` + `alembic check`: no drift.
- WebRTC E2E (`voice_e2e_smoke.py`): run 1 FAIL (greeting proper-noun check only, all others
  true), then runs 2, 3 and final PASS; barge-in PASS. Latency (end of speech → start of reply)
  3.07 / 2.80 / 2.79 / 3.04 / 2.61 s.
- Browser (`voice_browser_smoke.py`): 2/2 PASS.

**Known limitations:** no live-LLM voice session yet (quota); no human-microphone session yet;
English only; unauthenticated local token endpoint; with free-tier LLMs, turn latency will be
dominated by the model.

**Next:** Phase 5 — Durable workflows (awaiting authorization).

---

## 2026-10-12 — Phase 4 follow-up: Kokoro TTS and GPU speech (disk freed by the user)

**Trigger:** disk space was no longer a constraint (69 GB free), so the two items deferred for disk
were evaluated: Kokoro (blueprint: "evaluate Kokoro if suitable") and GPU speech recognition.

**Kokoro evaluation (CPU, 5 agent-style sentences; intelligibility = Whisper `small.en` WER)**
- Piper `en_US-ljspeech-medium`: RTF 0.048, first sentence 0.18 s, WER 0.14.
- Kokoro v1.0 fp32 `af_heart`: RTF 0.557, first sentence 1.88 s, WER **0.061**; `af_bella` WER
  0.107; the int8 model was slower on this CPU (RTF 0.60).
- Licence: Kokoro-82M weights Apache-2.0, trained on permissive or public-domain audio (two small
  CC BY datasets, Japanese and French, credited by the authors).
- Kokoro on GPU (onnxruntime-gpu CUDA provider): 0.29–0.32 s for 3.8 s of audio.

**GPU STT (Whisper, 11.5 s utterance):** `base.en` CPU int8 0.88–1.01 s; `base.en` CUDA float16
0.22–0.25 s (same transcript); `small.en` CUDA mis-transcribed the phone number; `medium.en` CUDA
no faster (0.76 s).

**Changes**
- `voice/gpu.py` (DLL registration for NVIDIA pip wheels on Windows; `auto` resolution),
  `voice/tts_kokoro.py` (`KokoroTTS`, `load_kokoro`), session manager selects engines and warms up
  models in `prewarm`. Settings: `STT_DEVICE=auto`, `STT_COMPUTE_TYPE=auto`, `TTS_ENGINE=auto`,
  `KOKORO_VOICE=af_heart`, `VOICE_IDLE_PROCESSES=1`. `scripts/enable_gpu.py` (pinned versions),
  `download_voice_models.py` now fetches Kokoro. Dependency: kokoro-onnx 0.6.1.
- Tests: Kokoro adapter through LiveKit's stream plumbing; device and engine resolution.

**Environment findings**
- onnxruntime-gpu 1.31 needs CUDA 13, while ctranslate2 4.8 needs CUDA 12. The CUDA 13 wheels
  install to `nvidia/cu13/...` (no clash), but both cuDNN 9 builds write `nvidia/cudnn/bin/cudnn64_9.dll`.
  Only `nvidia-cudnn-cu13` is installed, and it was verified to serve both libraries in one process.
- `onnxruntime-gpu` and CPU `onnxruntime` (required by silero/piper/faster-whisper) share one
  module, so `enable_gpu.py` must be re-run after dependency installs. The code falls back to CPU
  without it.

**Bug found and fixed:** with default settings LiveKit pre-spawned several job processes, each
loading Whisper and Kokoro onto the GPU (3,943 / 4,096 MiB used while idle). STT then took 5.8 s and
a turn 13.8 s, and the browser run failed to transcribe. `VOICE_IDLE_PROCESSES=1` → 793 MiB idle.

**Results (actual)**
- GPU Whisper + Kokoro: WebRTC basic PASS ×2 (latency 1.74 s, 1.75 s), barge-in PASS (2.13 s;
  3.31 s after the interruption), browser PASS. STT 0.30–0.43 s, TTS 0.34–0.99 s per sentence.
- `pytest` 275 passed; `ruff check` / `ruff format --check` clean;
  `mypy --config-file backend/pyproject.toml backend/app` clean (58 files).

**Still pending:** voice with the live Gemini/Groq chain, and a human-microphone session.

---

## 2026-10-12 — Phase 5: durable workflows (Temporal)

**Added**
- Docker Compose `temporal` service (`temporalio/temporal:1.9.1`: Server 1.32.0, UI 2.54.1; dev
  server with SQLite at `/data` on a named volume, run as root because the volume is root-owned;
  127.0.0.1:7233 / 8233). SDK `temporalio` 1.34.0.
- Models + migration `868d5e8c91bc`: `workflow_executions` (outbox/status; unique deterministic
  `workflow_id`), `staff_notifications` (idempotent per `(firm_id, dedupe_key)`), `(firm_id, id)`
  unique key on escalations (composite-FK target), escalation reason `INCOMPLETE_INTAKE`. The
  CHECK constraint was widened by hand: autogenerate does not detect it, and `op.f()` was needed to
  stop the naming convention double-prefixing the name.
- Domain: `workflow_service` (request, claim with SKIP LOCKED, mark started/finished),
  `notification_service`, `appointment_service.find_appointment_by_key`. `create_escalation` and
  `end_call_session` request their workflows in the same transaction. `INCOMPLETE_INTAKE` is
  system-only with fixed priority NORMAL. Firm `handoff` policy (delivery attempts, reviewer and
  fallback roles, acknowledgement minutes by priority, reminders, incomplete follow-up delay).
- `app/workflows/`: `types.py`, `faults.py`, `activities.py` (`IntakeActivities`), `workflows.py`
  (`HandoffWorkflow`, `BookingWorkflow`, `PostCallWorkflow`), `worker.py` (worker + dispatcher),
  `client.py` (booking from the sync tool). `scripts/temporal_worker.py`,
  `scripts/workflow_restart_test.py`.
- `book_consultation` uses `BookingWorkflow` when `ToolContext.durable_booking` (the conversation
  sets it from `BOOKING_VIA_WORKFLOW`; tests keep the direct path). New error code
  `BOOKING_NOT_CONFIRMED`.
- Tests: `test_workflows.py` (11, time-skipping env + real DB), 5 durable-booking tool tests.

**Decisions**
- Transactional outbox rather than starting workflows from request code: business writes never
  depend on Temporal being up, and nothing requested is lost.
- `REJECT_DUPLICATE` workflow-ID policy + deterministic IDs: re-dispatch after a lost commit is
  recognised (`WorkflowAlreadyStartedError`), never a second run.
- The reviewer channel is an adapter (`notify_reviewers`, simulated by a DB inbox). The fallback
  alert deliberately bypasses that channel.
- Booking: one activity attempt per workflow attempt, with a state check between attempts. This
  is the blueprint's "timeout → check state before retrying".

**Problems found and fixed**
- Time-skipping test server: awaiting `handle.result()` returned "No completion event found". The
  workflow history showed a healthy run waiting on its timer, so tests now skip time explicitly
  (`env.sleep`) and poll `describe()`. `list_workflows` is unimplemented there, so the no-duplicate
  check compares run IDs.
- Post-call follow-up initially used a non-existent cross-tenant intake lookup; `firm_id` is now
  carried in the plan and the normal tenant-scoped lookup is used.

**Tests and validations executed (actual)**
- `pytest` 291 passed; ruff check + format clean; `mypy --config-file backend/pyproject.toml
  backend/app` clean (68 files); `alembic upgrade head` + `alembic check` no drift.
- `workflow_restart_test.py` run 1: booking PASS (state-check recovery 10.9 s after the kill, 1
  appointment), handoff PASS (NEW, REMINDER, UNACKNOWLEDGED_FALLBACK once each), outbox PASS
  (REQUESTED while Temporal was down, then started). Run 2: identical results, 3/3 PASS.
- Voice regression after Phase 5: 2/2 PASS (1.81 s, 3.16 s; stage timings normal).

**Known limitations:** see Phase 5 in `IMPLEMENTATION_PROGRESS.md` (simulated reviewer channel, no
dashboard signal yet, dev-server Temporal, direct-booking fallback when Temporal is unreachable).

**Next:** Phase 6 — Evaluation harness (awaiting authorization).
