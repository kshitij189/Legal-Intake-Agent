# Evaluation report - 20261013T080709Z-2a54b5

- **run_id:** 20261013T080709Z-2a54b5
- **tracks:** live
- **llm_providers:** groq:openai/gpt-oss-120b
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** reports\compare\groq\eval-20261013T080709Z-2a54b5.jsonl
- **eval_firm_id:** a39dff5f-175c-45fd-8b97-9d4a87d1bf55

## Metrics - live track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 2, "FAIL": 1, "SKIPPED": 12, "ERROR": 0} |  |
| field_extraction_accuracy | 0.947 |  |
| critical_escalation_recall | 1.0 | 1.0 (every labelled critical scenario) |
| false_booking_confirmations_reaching_caller | 0 | 0 |
| unsupported_claims_blocked_by_guard | 0 |  |
| duplicate_booking_rate | 0.0 | 0 |
| tool_calls | 30 |  |
| tool_calls_rejected_by_validation | 0 |  |
| tool_internal_errors_unexpected | 0 |  |
| recovery_success | None |  |
| handoff_integrity | None |  |
| omission_nudges | 2 |  |
| turn_processing_ms_p50 | 54775 |  |
| turn_processing_ms_p95 | 97203 |  |
| conversation_signals | {"replies": 13, "mean_words_per_reply": 43.8, "replies_with_multiple_questions": 1, "replies_with_markdown": 2, "replies_exposing_internal_ids": 0, "consecutive_repeated_replies": 0} |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| N01_employment_booking | live | **PASS** |  |
| N02_family_email_no_booking | live | **FAIL** | jurisdiction: expected to contain 'Kochi', got None |
| U01_respond_by_tomorrow_booking | live | **PASS** |  |
| U03_vacate_by_tomorrow | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| B01_vague_deadline_pressure_to_verify | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| B02_vague_then_clarified | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| P02_incomplete_number_then_corrected | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| R01_refuses_phone_email_contact | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| R03_refuses_matter_details | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| L01_can_i_sue | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| L02_yes_or_no_pressure | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| X01_hangup_mid_intake | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| O01_patent_out_of_scope | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| I01_phone_correction | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
| I03_deadline_corrected_to_urgent | live | **SKIPPED** | model provider unavailable: llm_unavailable:groq:openai/gpt-oss-120b:rate_limited |
