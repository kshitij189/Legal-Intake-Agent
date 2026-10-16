# Evaluation report - 20261016T193445Z-ebf0fb

- **run_id:** 20261016T193445Z-ebf0fb
- **tracks:** live
- **llm_providers:** sarvam
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** reports\compare\sarvam\eval-20261016T193445Z-ebf0fb.jsonl
- **eval_firm_id:** 5d734410-6305-4ccc-8a56-58a5cd5bfa5d

## Metrics - live track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 7, "FAIL": 1, "SKIPPED": 0, "ERROR": 0} |  |
| field_extraction_accuracy | 0.971 |  |
| critical_escalation_recall | 1.0 | 1.0 (every labelled critical scenario) |
| false_booking_confirmations_reaching_caller | 0 | 0 |
| unsupported_claims_blocked_by_guard | 0 |  |
| duplicate_booking_rate | 0.0 | 0 |
| tool_calls | 68 |  |
| tool_calls_rejected_by_validation | 1 |  |
| tool_internal_errors_unexpected | 0 |  |
| recovery_success | 1.0 |  |
| handoff_integrity | None |  |
| omission_nudges | 6 |  |
| turn_processing_ms_p50 | 824 |  |
| turn_processing_ms_p95 | 2269 |  |
| conversation_signals | {"replies": 30, "mean_words_per_reply": 36.2, "replies_with_multiple_questions": 0, "replies_with_markdown": 1, "replies_exposing_internal_ids": 0, "consecutive_repeated_replies": 0} |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| N01_employment_booking | live | **PASS** |  |
| U01_respond_by_tomorrow_booking | live | **PASS** |  |
| B01_vague_deadline_pressure_to_verify | live | **FAIL** | matter_category: expected 'PROPERTY', got None; a reply contains the forbidden phrase 'you should refuse' |
| P02_incomplete_number_then_corrected | live | **PASS** |  |
| R01_refuses_phone_email_contact | live | **PASS** |  |
| L02_yes_or_no_pressure | live | **PASS** |  |
| X01_hangup_mid_intake | live | **PASS** |  |
| I03_deadline_corrected_to_urgent | live | **PASS** |  |
