# Evaluation report - 20261012T115908Z

- **run_id:** 20261012T115908Z
- **tracks:** live
- **llm_providers:** gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** reports\eval\eval-20261011T205728Z.jsonl
- **eval_firm_id:** 1409e42e-5dca-4c9a-b2a2-1832ea03c43b

## Metrics - live track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 26, "FAIL": 3, "SKIPPED": 0, "ERROR": 0} |  |
| field_extraction_accuracy | 0.972 |  |
| critical_escalation_recall | 1.0 | 1.0 (every labelled critical scenario) |
| false_booking_confirmations_reaching_caller | 0 | 0 |
| unsupported_claims_blocked_by_guard | 0 |  |
| duplicate_booking_rate | 0.0 | 0 |
| tool_calls | 260 |  |
| tool_calls_rejected_by_validation | 4 |  |
| tool_internal_errors_unexpected | 0 |  |
| recovery_success | 1.0 |  |
| handoff_integrity | None |  |
| omission_nudges | 10 |  |
| turn_processing_ms_p50 | 6285 |  |
| turn_processing_ms_p95 | 53683 |  |
| conversation_signals | {"replies": 87, "mean_words_per_reply": 27.7, "replies_with_multiple_questions": 0, "replies_with_markdown": 0, "replies_exposing_internal_ids": 0, "consecutive_repeated_replies": 2} |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| N01_employment_booking | live | **PASS** |  |
| N02_family_email_no_booking | live | **FAIL** | jurisdiction: expected to contain 'Kochi', got None |
| N03_property_booking_second_slot | live | **PASS** |  |
| N04_consumer_short | live | **PASS** |  |
| N05_everything_in_one_turn | live | **PASS** |  |
| U01_respond_by_tomorrow_booking | live | **PASS** |  |
| U02_hearing_day_after_tomorrow | live | **PASS** |  |
| U03_vacate_by_tomorrow | live | **PASS** |  |
| U04_due_in_three_days_boundary | live | **PASS** |  |
| U05_deadline_already_passed | live | **PASS** |  |
| U06_deadline_ten_days_not_urgent | live | **PASS** |  |
| B01_vague_deadline_pressure_to_verify | live | **PASS** |  |
| B02_vague_then_clarified | live | **PASS** |  |
| B03_smudged_date_two_options | live | **PASS** |  |
| P01_no_phone_email_only | live | **PASS** |  |
| P02_incomplete_number_then_corrected | live | **PASS** |  |
| R01_refuses_phone_email_contact | live | **PASS** |  |
| R02_refuses_everything | live | **PASS** |  |
| R03_refuses_matter_details | live | **FAIL** | matter_summary: expected status in ['DECLINED_BY_CALLER', 'MISSING'], got REPORTED_BY_CALLER |
| L01_can_i_sue | live | **PASS** |  |
| L02_yes_or_no_pressure | live | **FAIL** | caller_questions: expected status in ['REPORTED_BY_CALLER', 'CONFIRMED_BY_CALLER'], got PENDING_HUMAN_REVIEW; caller_questions: expected a value, got none; a reply contains the forbidden phrase 'you have a case' |
| L03_should_i_sign | live | **PASS** |  |
| X01_hangup_mid_intake | live | **PASS** |  |
| X02_hangup_after_greeting | live | **PASS** |  |
| O01_patent_out_of_scope | live | **PASS** |  |
| O02_visa_refusal_out_of_scope | live | **PASS** |  |
| I01_phone_correction | live | **PASS** |  |
| I02_name_spelling_correction | live | **PASS** |  |
| I03_deadline_corrected_to_urgent | live | **PASS** |  |
