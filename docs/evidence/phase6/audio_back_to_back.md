# Evaluation report - 20261011T204306Z

- **run_id:** 20261011T204306Z
- **tracks:** audio
- **llm_providers:** scripted (system)
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** C:\Users\rog\Downloads\Legal_Intake_agent\reports\eval\eval-20261011T204306Z.jsonl
- **eval_firm_id:** 06c9a27c-4d76-4fb4-8e72-6b8faff87f12

## Metrics - audio track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 3, "FAIL": 6, "ERROR": 0} |  |
| caller_wer_mean | 0.462 |  |
| caller_wer_by_scenario | {"AU01_piper_clean": 0.0, "AU02_kokoro_us_female": 0.087, "AU03_kokoro_us_male": 0.038, "AU04_kokoro_british_male": 0.038, "AU05_hindi_voice_english_speech": 0.696, "AU06_fast_speech": 1.0, "AU07_noisy_line": 0.8, "AU08_mid_sentence_pause": 0.5, "AU09_barge_in": 1.0} |  |
| agent_speech_wer_mean | 0.384 |  |
| keyword_recall | 0.516 |  |
| response_latency_s_p50 | 2.88 |  |
| response_latency_s_p95 | 6.38 |  |
| barge_in_handled | 0/1 |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| AU01_piper_clean | audio | **PASS** |  |
| AU02_kokoro_us_female | audio | **FAIL** | keywords not recognized: ['9447055667'] |
| AU03_kokoro_us_male | audio | **PASS** |  |
| AU04_kokoro_british_male | audio | **PASS** |  |
| AU05_hindi_voice_english_speech | audio | **FAIL** | caller WER 0.696 > 0.35; keywords not recognized: ['Deepa', 'custody', '9008070060']; response latency above 5.0s: [7.594149800017476, 5.6417623000452295] |
| AU06_fast_speech | audio | **FAIL** | no caller speech was transcribed and stored; caller WER 1.0 > 0.3; keywords not recognized: ['Neha', 'sofa', '9811100999'] |
| AU07_noisy_line | audio | **FAIL** | caller WER 0.8 > 0.35; keywords not recognized: ['Imran', 'landlord', 'tomorrow']; response latency above 5.0s: [6.3787435999838635] |
| AU08_mid_sentence_pause | audio | **FAIL** | caller WER 0.5 > 0.3; keywords not recognized: ['Rohan Das', 'summons'] |
| AU09_barge_in | audio | **FAIL** | no caller speech was transcribed and stored; caller WER 1.0 > 0.3; keywords not recognized: ['Farhan', 'laptop', '9000011122']; response latency above 5.0s: [5.216192200023215]; barge-in not handled (agent interrupted flags [False]) |
