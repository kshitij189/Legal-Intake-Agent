# Evaluation report - 20261013T073652Z

- **run_id:** 20261013T073652Z
- **tracks:** live
- **llm_providers:** gemini:gemini-3.5-flash-lite
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** reports\compare\gemini\eval-20261013T073652Z.jsonl
- **eval_firm_id:** 2a08ab06-e024-4fcf-83b3-aaa178fd2bb1

## Metrics - live track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 6, "FAIL": 0, "SKIPPED": 9, "ERROR": 0} |  |
| field_extraction_accuracy | 1.0 |  |
| critical_escalation_recall | None | 1.0 (every labelled critical scenario) |
| false_booking_confirmations_reaching_caller | 0 | 0 |
| unsupported_claims_blocked_by_guard | 0 |  |
| duplicate_booking_rate | 0.0 | 0 |
| tool_calls | 59 |  |
| tool_calls_rejected_by_validation | 0 |  |
| tool_internal_errors_unexpected | 0 |  |
| recovery_success | None |  |
| handoff_integrity | None |  |
| omission_nudges | 1 |  |
| turn_processing_ms_p50 | 3560 |  |
| turn_processing_ms_p95 | 7094 |  |
| conversation_signals | {"replies": 23, "mean_words_per_reply": 28.5, "replies_with_multiple_questions": 0, "replies_with_markdown": 0, "replies_exposing_internal_ids": 0, "consecutive_repeated_replies": 0} |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| N01_employment_booking | live | **PASS** |  |
| N02_family_email_no_booking | live | **PASS** |  |
| U01_respond_by_tomorrow_booking | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| U03_vacate_by_tomorrow | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| B01_vague_deadline_pressure_to_verify | live | **PASS** |  |
| B02_vague_then_clarified | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| P02_incomplete_number_then_corrected | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| R01_refuses_phone_email_contact | live | **PASS** |  |
| R03_refuses_matter_details | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| L01_can_i_sue | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| L02_yes_or_no_pressure | live | **PASS** |  |
| X01_hangup_mid_intake | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| O01_patent_out_of_scope | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
| I01_phone_correction | live | **PASS** |  |
| I03_deadline_corrected_to_urgent | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.5-flash-lite:rate_limited |
