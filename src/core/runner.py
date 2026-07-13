from __future__ import annotations

import json
from pathlib import Path

from .matcher import load_cases, match_case_to_run
from .normalizer import normalize_capture, write_jsonl
from .report import render_report
from .scorer import score_case


def run_evaluation(
    suite: str,
    events_path: Path,
    out_dir: Path,
    run_label: str,
    image_detail_limit: int = 10,
    profile: dict | None = None,
) -> dict[str, Path]:
    cases = load_cases(suite, profile=profile)
    runs, trace = normalize_capture(
        events_path,
        image_detail_limit=image_detail_limit,
        tool_aliases=(profile or {}).get("tool_aliases"),
        skill_map=(profile or {}).get("skill_map"),
        tool_registry=(profile or {}).get("tool_registry"),
        normalizer_config=(profile or {}).get("normalizer_map"),
    )
    results = [score_case(case, match_case_to_run(case, runs), profile=profile) for case in cases]

    normalized_runs_path = out_dir / f"{run_label}_normalized_runs.jsonl"
    normalized_trace_path = out_dir / f"{run_label}_normalized_trace.jsonl"
    results_path = out_dir / f"{run_label}_eval_results.jsonl"
    report_path = out_dir / f"{run_label}_eval_report.md"

    write_jsonl(normalized_runs_path, runs)
    write_jsonl(normalized_trace_path, trace)
    write_jsonl(results_path, [result.__dict__ for result in results])
    render_report(suite, cases, runs, results, report_path, profile=profile)

    return {
        "normalized_runs": normalized_runs_path,
        "normalized_trace": normalized_trace_path,
        "eval_results": results_path,
        "eval_report": report_path,
    }
