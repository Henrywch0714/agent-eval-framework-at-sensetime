from __future__ import annotations

import json
from pathlib import Path

from .error_gate import AgentDataError, check_agent_data_errors, filter_evaluable_runs
from .llm_assistant import LLMAssistantConfig, judge_case, summarize_deductions
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
    llm_assistant_config: LLMAssistantConfig | None = None,
) -> dict[str, Path]:
    raw_error_check = check_agent_data_errors(events_path, profile=profile)
    cases = load_cases(suite, profile=profile)
    runs, trace = normalize_capture(
        events_path,
        image_detail_limit=image_detail_limit,
        tool_aliases=(profile or {}).get("tool_aliases"),
        skill_map=(profile or {}).get("skill_map"),
        tool_registry=(profile or {}).get("tool_registry"),
        normalizer_config=(profile or {}).get("normalizer_map"),
    )
    runs, trace, skipped_error_check = filter_evaluable_runs(runs, trace)
    if raw_error_check.has_error and not runs:
        raise AgentDataError(raw_error_check)
    if skipped_error_check.has_error and not runs:
        raise AgentDataError(skipped_error_check)

    results = []
    for case in cases:
        matched_run = match_case_to_run(case, runs)
        if matched_run is None:
            continue
        judge = judge_case(case, matched_run, profile, llm_assistant_config) if llm_assistant_config else None
        result = score_case(case, matched_run, profile=profile, llm_assistant=judge)
        _attach_llm_deduction_summary(result, llm_assistant_config, enabled=True)
        results.append(result)

    normalized_runs_path = out_dir / f"{run_label}_normalized_runs.jsonl"
    normalized_trace_path = out_dir / f"{run_label}_normalized_trace.jsonl"
    results_path = out_dir / f"{run_label}_eval_results.jsonl"
    report_path = out_dir / f"{run_label}_eval_report.md"

    write_jsonl(normalized_runs_path, runs)
    write_jsonl(normalized_trace_path, trace)
    write_jsonl(results_path, [result.__dict__ for result in results])
    render_report(suite, cases, runs, results, report_path, profile=profile, llm_assistant_config=llm_assistant_config)

    return {
        "normalized_runs": normalized_runs_path,
        "normalized_trace": normalized_trace_path,
        "eval_results": results_path,
        "eval_report": report_path,
    }


def _attach_llm_deduction_summary(result, config: LLMAssistantConfig | None, enabled: bool) -> None:
    if not enabled or not config or not config.enabled:
        return
    summary = result.deduction_summary or {}
    interface = summary.get("llm_interface") or {}
    payload = interface.get("input") or {}
    if not payload:
        return
    llm_summary = summarize_deductions(payload, config)
    interface.update(llm_summary)
    interface.setdefault("input", payload)
    summary["llm_interface"] = interface
    result.deduction_summary = summary
