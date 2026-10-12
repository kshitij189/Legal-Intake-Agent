# Evaluation report - 20261012T123436Z

- **run_id:** 20261012T123436Z
- **tracks:** audio
- **llm_providers:** scripted (system)
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** C:\Users\rog\Downloads\Legal_Intake_agent\reports\eval\eval-20261012T123436Z.jsonl
- **eval_firm_id:** da92c22e-0149-42a6-8a4b-e1cddf08e3bb
- **audio_worker_tts:** configured (auto)
- **audio_back_to_back:** False

## Metrics - audio track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 5, "FAIL": 4, "ERROR": 0} |  |
| caller_wer_mean | 0.084 |  |
| caller_wer_by_scenario | {"AU01_piper_clean": 0.0, "AU02_kokoro_us_female": 0.087, "AU03_kokoro_us_male": 0.038, "AU04_kokoro_british_male": 0.038, "AU05_hindi_voice_english_speech": 0.087, "AU06_fast_speech": 0.167, "AU07_noisy_line": 0.05, "AU08_mid_sentence_pause": 0.286, "AU09_barge_in": 0.0} |  |
| agent_speech_wer_mean | 0.033 |  |
| keyword_recall | 0.839 |  |
| response_latency_s_p50 | 2.42 |  |
| response_latency_s_p95 | 2.88 |  |
| barge_in_handled | 1/1 |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| AU01_piper_clean | audio | **PASS** |  |
| AU02_kokoro_us_female | audio | **FAIL** | keywords not recognized: ['9447055667'] |
| AU03_kokoro_us_male | audio | **PASS** |  |
| AU04_kokoro_british_male | audio | **PASS** |  |
| AU05_hindi_voice_english_speech | audio | **FAIL** | keywords not recognized: ['9008070060'] |
| AU06_fast_speech | audio | **FAIL** | keywords not recognized: ['Neha', '9811100999'] |
| AU07_noisy_line | audio | **PASS** |  |
| AU08_mid_sentence_pause | audio | **FAIL** | keywords not recognized: ['Rohan Das'] |
| AU09_barge_in | audio | **PASS** |  |
