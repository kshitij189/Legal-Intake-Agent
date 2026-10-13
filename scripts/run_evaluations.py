"""Phase 6 evaluation harness - one command, exact PASS/FAIL per scenario, JSON + Markdown report.

Tracks (blueprint §13):
  system  scripted model + injected faults: proves the system guarantees, free and deterministic
  live    the real model chain (LLM_PROVIDERS): measures model quality; quota exhaustion = SKIPPED
  audio   Mode B: synthesized caller speech over LiveKit/WebRTC (see app/evaluation/audio_runner)
  all     every track above

Usage:
  .venv\\Scripts\\python scripts/run_evaluations.py --track system
  .venv\\Scripts\\python scripts/run_evaluations.py --track live --resume reports/eval/live.jsonl

Do not run scripts/temporal_worker.py at the same time: the evaluation dispatches its own firm's
outbox to its own task queue. Exit code 0 only when nothing FAILed or ERRORed.
"""

import argparse
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.agent.llm_client import build_llm_client
from app.config import REPO_ROOT, get_settings
from app.db.session import make_engine, make_session_factory
from app.evaluation.metrics import (
    ERROR,
    FAIL,
    PASS,
    SKIPPED,
    ScenarioResult,
    aggregate,
    result_from_dict,
)
from app.evaluation.report import write_reports
from app.evaluation.runner import EvalContext, create_eval_firm, retire_eval_firm, run_scenario
from app.evaluation.scenario import Scenario, load_scenarios

SCENARIOS = REPO_ROOT / "fixtures" / "scenarios" / "transcript_scenarios.json"
AUDIO_SCENARIOS = REPO_ROOT / "fixtures" / "scenarios" / "audio_scenarios.json"
REPORTS = REPO_ROOT / "reports" / "eval"


def load_progress(path: Path | None) -> dict[tuple[str, str], ScenarioResult]:
    """Finished results (PASS/FAIL) from an earlier run; SKIPPED/ERROR are retried."""
    if path is None or not path.exists():
        return {}
    done = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = result_from_dict(json.loads(line))
            if r.status in (PASS, FAIL):
                done[(r.track, r.scenario_id)] = r
    return done


def print_result(r: ScenarioResult) -> None:
    print(f"{r.status:7} {r.track:6} {r.scenario_id}  ({r.duration_s}s)", flush=True)
    for p in r.problems:
        print(f"          - {p}", flush=True)
    if r.note:
        print(f"          note: {r.note}", flush=True)


def select(scenarios: list[Scenario], args: argparse.Namespace) -> list[Scenario]:
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s.id in wanted]
    if args.category:
        scenarios = [s for s in scenarios if s.category == args.category]
    return scenarios[: args.limit] if args.limit else scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--track", choices=["system", "live", "audio", "all"], default="system")
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS)
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", type=Path, help="JSONL progress file to continue")
    parser.add_argument("--report-dir", type=Path, default=REPORTS)
    parser.add_argument("--pause", type=float, default=4.0, help="seconds between live scenarios")
    parser.add_argument(
        "--audio-back-to-back",
        action="store_true",
        help="audio: start speaking right after the greeting, while the worker is still warming "
        "its replacement process (reproduces the GPU-contention finding)",
    )
    parser.add_argument(
        "--audio-tts",
        choices=["auto", "piper", "kokoro"],
        help="audio: TTS engine for the voice worker (default: the configured TTS_ENGINE). "
        "'piper' keeps TTS off the GPU, isolating speech recognition from GPU-memory pressure",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    if not args.verbose:
        # Injected faults log expected tracebacks; the report records what actually happened.
        for noisy in ("temporalio", "app"):
            logging.getLogger(noisy).setLevel(logging.CRITICAL)

    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    # Unique even for runs started in the same second: each run owns (and retires) its own firm.
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    tracks = ["system", "live", "audio"] if args.track == "all" else [args.track]
    progress_path = args.resume or args.report_dir / f"eval-{run_id}.jsonl"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_progress(args.resume)

    firm_id = create_eval_firm(sessions, run_id)
    ctx = EvalContext(settings, sessions, firm_id, live_llm=lambda: build_llm_client(settings))
    scenarios = select(load_scenarios(args.scenarios), args)
    by_id = {s.id: s for s in scenarios}
    results: list[ScenarioResult] = []

    with progress_path.open("a", encoding="utf-8") as progress:
        for track in [t for t in tracks if t != "audio"]:
            for i, scenario in enumerate(s for s in scenarios if track in s.tracks):
                if (track, scenario.id) in done:
                    results.append(done[(track, scenario.id)])
                    continue
                if track == "live" and i:
                    time.sleep(args.pause)  # stay under provider rate limits
                result = run_scenario(scenario, track, ctx)  # type: ignore[arg-type]
                results.append(result)
                progress.write(json.dumps(asdict(result), default=str) + "\n")
                progress.flush()
                print_result(result)

    metrics = {
        t: aggregate([r for r in results if r.track == t], by_id)
        for t in tracks
        if t != "audio" and any(r.track == t for r in results)
    }
    if "audio" in tracks:
        from app.evaluation.audio_runner import run_audio_track

        audio_results, audio_metrics = run_audio_track(settings, AUDIO_SCENARIOS, args)
        results += audio_results
        metrics["audio"] = audio_metrics
        for r in audio_results:
            print_result(r)

    meta = {
        "run_id": run_id,
        "tracks": ",".join(tracks),
        "llm_providers": settings.llm_providers if "live" in tracks else "scripted (system)",
        "scenario_file": str(args.scenarios.relative_to(REPO_ROOT)),
        "progress_file": str(progress_path),
        "eval_firm_id": str(firm_id),
    }
    if "audio" in tracks:
        meta["audio_worker_tts"] = args.audio_tts or f"configured ({settings.tts_engine})"
        meta["audio_back_to_back"] = args.audio_back_to_back
    retire_eval_firm(sessions, firm_id)  # leftovers must not reach the regular worker
    json_path, md_path = write_reports(
        args.report_dir, f"eval-{run_id}-{args.track}", meta, results, metrics
    )
    counts = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, SKIPPED, ERROR)}
    print(f"\n{counts}\nreport: {json_path}\n        {md_path}")
    return 0 if counts[FAIL] == 0 and counts[ERROR] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
