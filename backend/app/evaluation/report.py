"""Evaluation reports: JSON (complete) and Markdown (readable), written side by side."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.evaluation.metrics import ScenarioResult

TARGETS = {
    "false_booking_confirmations_reaching_caller": "0",
    "duplicate_booking_rate": "0",
    "critical_escalation_recall": "1.0 (every labelled critical scenario)",
}


def write_reports(
    directory: Path,
    name: str,
    meta: dict[str, Any],
    results: list[ScenarioResult],
    metrics: dict[str, dict[str, Any]],
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{name}.json", directory / f"{name}.md"
    json_path.write_text(
        json.dumps(
            {"meta": meta, "metrics": metrics, "results": [asdict(r) for r in results]},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    md_path.write_text(markdown(meta, results, metrics), encoding="utf-8")
    return json_path, md_path


def markdown(
    meta: dict[str, Any], results: list[ScenarioResult], metrics: dict[str, dict[str, Any]]
) -> str:
    lines = [f"# Evaluation report - {meta.get('run_id')}", ""]
    lines += [f"- **{k}:** {v}" for k, v in meta.items()]
    for track, values in metrics.items():
        lines += [
            "",
            f"## Metrics - {track} track",
            "",
            "| Metric | Value | Target |",
            "|---|---|---|",
        ]
        for key, value in values.items():
            shown = json.dumps(value) if isinstance(value, dict) else value
            lines.append(f"| {key} | {shown} | {TARGETS.get(key, '')} |")
    lines += [
        "",
        "## Scenarios",
        "",
        "| Scenario | Track | Status | Details |",
        "|---|---|---|---|",
    ]
    for r in results:
        details = "; ".join(r.problems) or (r.note or "")
        lines.append(
            f"| {r.scenario_id} | {r.track} | **{r.status}** | {details.replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"
