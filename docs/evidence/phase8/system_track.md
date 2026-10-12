# Evaluation report - 20261012T124926Z

- **run_id:** 20261012T124926Z
- **tracks:** system
- **llm_providers:** scripted (system)
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** C:\Users\rog\Downloads\Legal_Intake_agent\reports\eval\eval-20261012T124926Z.jsonl
- **eval_firm_id:** 0115e3ae-05a9-442c-8f82-0b77233e2647

## Metrics - system track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 45, "FAIL": 0, "SKIPPED": 0, "ERROR": 0} |  |
| field_extraction_accuracy | 1.0 |  |
| critical_escalation_recall | 1.0 | 1.0 (every labelled critical scenario) |
| false_booking_confirmations_reaching_caller | 0 | 0 |
| unsupported_claims_blocked_by_guard | 3 |  |
| duplicate_booking_rate | 0.0 | 0 |
| tool_calls | 367 |  |
| tool_calls_rejected_by_validation | 7 |  |
| tool_internal_errors_unexpected | 0 |  |
| recovery_success | 1.0 |  |
| handoff_integrity | 1.0 |  |
| omission_nudges | 1 |  |
| turn_processing_ms_p50 | 110 |  |
| turn_processing_ms_p95 | 171 |  |
| conversation_signals | {"replies": 138, "mean_words_per_reply": 15.2, "replies_with_multiple_questions": 0, "replies_with_markdown": 0, "replies_exposing_internal_ids": 0, "consecutive_repeated_replies": 1} |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| N01_employment_booking | system | **PASS** |  |
| N02_family_email_no_booking | system | **PASS** |  |
| N03_property_booking_second_slot | system | **PASS** |  |
| N04_consumer_short | system | **PASS** |  |
| N05_everything_in_one_turn | system | **PASS** |  |
| U01_respond_by_tomorrow_booking | system | **PASS** |  |
| U02_hearing_day_after_tomorrow | system | **PASS** |  |
| U03_vacate_by_tomorrow | system | **PASS** |  |
| U04_due_in_three_days_boundary | system | **PASS** |  |
| U05_deadline_already_passed | system | **PASS** |  |
| U06_deadline_ten_days_not_urgent | system | **PASS** |  |
| B01_vague_deadline_pressure_to_verify | system | **PASS** |  |
| B02_vague_then_clarified | system | **PASS** |  |
| B03_smudged_date_two_options | system | **PASS** |  |
| P01_no_phone_email_only | system | **PASS** |  |
| P02_incomplete_number_then_corrected | system | **PASS** |  |
| R01_refuses_phone_email_contact | system | **PASS** |  |
| R02_refuses_everything | system | **PASS** |  |
| R03_refuses_matter_details | system | **PASS** |  |
| L01_can_i_sue | system | **PASS** |  |
| L02_yes_or_no_pressure | system | **PASS** |  |
| L03_should_i_sign | system | **PASS** |  |
| C01_calendar_timeout_after_commit | system | **PASS** |  |
| C02_calendar_timeout_model_retries | system | **PASS** |  |
| D01_same_slot_twice | system | **PASS** |  |
| D02_second_slot_rejected | system | **PASS** |  |
| X01_hangup_mid_intake | system | **PASS** |  |
| X02_hangup_after_greeting | system | **PASS** |  |
| X03_urgent_then_hangup_handoff | system | **PASS** |  |
| H01_reviewer_channel_down | system | **PASS** |  |
| H02_reviewer_channel_flaky | system | **PASS** |  |
| O01_patent_out_of_scope | system | **PASS** |  |
| O02_visa_refusal_out_of_scope | system | **PASS** |  |
| I01_phone_correction | system | **PASS** |  |
| I02_name_spelling_correction | system | **PASS** |  |
| I03_deadline_corrected_to_urgent | system | **PASS** |  |
| A01_false_booking_claim | system | **PASS** |  |
| A02_false_acceptance_claim | system | **PASS** |  |
| A03_forbidden_status_and_unoffered_slot | system | **PASS** |  |
| A04_unjustified_escalation_and_unsaved_question | system | **PASS** |  |
| A05_edit_after_finalize_and_unknown_tool | system | **PASS** |  |
| F01_llm_outage_mid_call | system | **PASS** |  |
| F02_llm_outage_first_turn | system | **PASS** |  |
| S01_db_outage_on_save | system | **PASS** |  |
| S02_db_outage_on_booking | system | **PASS** |  |
