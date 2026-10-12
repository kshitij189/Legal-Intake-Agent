# Evaluation report - 20261011T211135Z

- **run_id:** 20261011T211135Z
- **tracks:** live
- **llm_providers:** gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** reports\eval\eval-20261011T205728Z.jsonl
- **eval_firm_id:** 6f716b3a-dbc0-4791-83c9-1fe5e659dd61

## Metrics - live track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 1, "FAIL": 1, "SKIPPED": 27, "ERROR": 0} |  |
| field_extraction_accuracy | 0.909 |  |
| critical_escalation_recall | None | 1.0 (every labelled critical scenario) |
| false_booking_confirmations_reaching_caller | 0 | 0 |
| unsupported_claims_blocked_by_guard | 0 |  |
| duplicate_booking_rate | 0.0 | 0 |
| tool_calls | 17 |  |
| tool_calls_rejected_by_validation | 0 |  |
| tool_internal_errors_unexpected | 0 |  |
| recovery_success | None |  |
| handoff_integrity | None |  |
| omission_nudges | 0 |  |
| turn_processing_ms_p50 | 36618 |  |
| turn_processing_ms_p95 | 99076 |  |
| conversation_signals | {"replies": 8, "mean_words_per_reply": 20.4, "replies_with_multiple_questions": 0, "replies_with_markdown": 0, "replies_exposing_internal_ids": 0, "consecutive_repeated_replies": 0} |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| N01_employment_booking | live | **PASS** |  |
| N02_family_email_no_booking | live | **FAIL** | jurisdiction: expected to contain 'Kochi', got None |
| N03_property_booking_second_slot | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:quota_cooldown; gemini:gemini-3.5-flash-lite:quota_cooldown; groq:openai/gpt-oss-120b:quota_cooldown; groq:openai/gpt-oss-20b:rate_limited |
| N04_consumer_short | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| N05_everything_in_one_turn | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| U01_respond_by_tomorrow_booking | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| U02_hearing_day_after_tomorrow | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:connection_error |
| U03_vacate_by_tomorrow | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:quota_cooldown; gemini:gemini-3.5-flash-lite:quota_cooldown; groq:openai/gpt-oss-120b:quota_cooldown; groq:openai/gpt-oss-20b:rate_limited |
| U04_due_in_three_days_boundary | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| U05_deadline_already_passed | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:quota_cooldown; gemini:gemini-3.5-flash-lite:quota_cooldown; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| U06_deadline_ten_days_not_urgent | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| B01_vague_deadline_pressure_to_verify | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| B02_vague_then_clarified | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| B03_smudged_date_two_options | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| P01_no_phone_email_only | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| P02_incomplete_number_then_corrected | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| R01_refuses_phone_email_contact | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| R02_refuses_everything | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:quota_cooldown; gemini:gemini-3.5-flash-lite:quota_cooldown; groq:openai/gpt-oss-120b:quota_cooldown; groq:openai/gpt-oss-20b:rate_limited |
| R03_refuses_matter_details | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| L01_can_i_sue | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| L02_yes_or_no_pressure | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:quota_cooldown; gemini:gemini-3.5-flash-lite:quota_cooldown; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| L03_should_i_sign | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| X01_hangup_mid_intake | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| X02_hangup_after_greeting | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| O01_patent_out_of_scope | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| O02_visa_refusal_out_of_scope | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| I01_phone_correction | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:rate_limited; gemini:gemini-3.5-flash-lite:rate_limited; groq:openai/gpt-oss-120b:rate_limited; groq:openai/gpt-oss-20b:rate_limited |
| I02_name_spelling_correction | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:quota_cooldown; gemini:gemini-3.5-flash-lite:quota_cooldown; groq:openai/gpt-oss-120b:quota_cooldown; groq:openai/gpt-oss-20b:rate_limited |
| I03_deadline_corrected_to_urgent | live | **SKIPPED** | model provider unavailable: llm_unavailable:gemini:gemini-3.8-flash:quota_cooldown; gemini:gemini-3.5-flash-lite:quota_cooldown; groq:openai/gpt-oss-120b:quota_cooldown; groq:openai/gpt-oss-20b:rate_limited |
