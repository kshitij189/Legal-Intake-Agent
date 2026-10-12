# Evaluation report - 20261011T213616Z

- **run_id:** 20261011T213616Z
- **tracks:** audio
- **llm_providers:** scripted (system)
- **scenario_file:** fixtures\scenarios\transcript_scenarios.json
- **progress_file:** C:\Users\rog\Downloads\Legal_Intake_agent\reports\eval\eval-20261011T213616Z.jsonl
- **eval_firm_id:** a05d9d56-1972-4b3f-8304-df5efc57c739
- **audio_worker_tts:** configured (auto)
- **audio_back_to_back:** False

## Metrics - audio track

| Metric | Value | Target |
|---|---|---|
| scenarios | {"PASS": 3, "FAIL": 6, "ERROR": 0} |  |
| caller_wer_mean | 0.41 |  |
| caller_wer_by_scenario | {"AU01_piper_clean": 0.0, "AU02_kokoro_us_female": 0.043, "AU03_kokoro_us_male": 0.038, "AU04_kokoro_british_male": 0.538, "AU05_hindi_voice_english_speech": 0.652, "AU06_fast_speech": 0.708, "AU07_noisy_line": 0.8, "AU08_mid_sentence_pause": 0.5, "AU09_barge_in": 0.409} |  |
| agent_speech_wer_mean | 0.34 |  |
| keyword_recall | 0.581 |  |
| response_latency_s_p50 | 1.77 |  |
| response_latency_s_p95 | 5.32 |  |
| barge_in_handled | 1/1 |  |

## Scenarios

| Scenario | Track | Status | Details |
|---|---|---|---|
| AU01_piper_clean | audio | **PASS** |  |
| AU02_kokoro_us_female | audio | **PASS** |  |
| AU03_kokoro_us_male | audio | **PASS** |  |
| AU04_kokoro_british_male | audio | **FAIL** | caller WER 0.538 > 0.2; keywords not recognized: ['deposit']; response latency above 5.0s: [5.19892609998351] |
| AU05_hindi_voice_english_speech | audio | **FAIL** | caller WER 0.652 > 0.35; keywords not recognized: ['Deepa', 'custody', '9008070060'] |
| AU06_fast_speech | audio | **FAIL** | caller WER 0.708 > 0.3; keywords not recognized: ['Neha', 'sofa', '9811100999'] |
| AU07_noisy_line | audio | **FAIL** | caller WER 0.8 > 0.35; keywords not recognized: ['Imran', 'landlord', 'tomorrow']; response latency above 5.0s: [5.322797000000719] |
| AU08_mid_sentence_pause | audio | **FAIL** | caller WER 0.5 > 0.3; keywords not recognized: ['Rohan Das', 'summons']; response latency above 5.0s: [10.903075800044462] |
| AU09_barge_in | audio | **FAIL** | caller WER 0.409 > 0.3; keywords not recognized: ['Farhan'] |
