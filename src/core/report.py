from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .llm_assistant import LLMAssistantConfig
from .schema import ScoreResult
from .scorer import get_pass_threshold, get_scoring_weights


def render_report(
    suite: str,
    cases: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    results: list[ScoreResult],
    out_path: Path,
    profile: dict[str, Any] | None = None,
    llm_assistant_config: LLMAssistantConfig | None = None,
) -> None:
    observed_results = [result for result in results if _is_observed_result(result)]
    passed = sum(1 for result in observed_results if result.passed)
    avg = round(sum(result.score for result in observed_results) / len(observed_results), 1) if observed_results else 0
    lines = [
        "# Site Agent Evaluation Report",
        "",
        "## 1. Run Info",
        f"- Generated At: {datetime.now().isoformat(timespec='seconds')}",
        f"- Profile: `{(profile or {}).get('profile_id') or 'default'}`",
        f"- Profile Kits: {_profile_kit_summary(profile or {})}",
        f"- Profile Validation: {_profile_validation_summary(profile or {})}",
        f"- Suite: `{suite}`",
        f"- Cases: {len(observed_results)}",
        f"- Normalized Runs: {len(runs)}",
        f"- Passed Cases: {passed}",
        f"- Average Score: {avg}",
        "",
        "## 2. Experiment Snapshot",
        "| Run | User Task | Search Type | Web Answer |",
        "|---|---|---|---|",
        *_experiment_snapshot_lines(runs),
        "",
        "## 3. LLM Assistant",
        *_llm_assistant_lines(llm_assistant_config, observed_results),
        "",
        "## 4. Scoring Weights",
        f"- Pass Threshold: {get_pass_threshold(profile)}",
        f"- Fail On Any Failure: {str(((profile or {}).get('scoring_policy') or {}).get('fail_on_any_failure', True)).lower()}",
        "",
        "| Dimension | Weight |",
        "|---|---:|",
    ]
    for key, value in get_scoring_weights(profile).items():
        lines.append(f"| {key} | {value:.2f} |")
    lines.extend(["", "## 5. Summary", "| Case | Run | Score | Passed | Failures |", "|---|---|---:|---|---|"])
    for result in observed_results:
        score_text = str(result.score)
        passed_text = str(result.passed).lower()
        failures_text = ", ".join(result.failure_types) if result.failure_types else "-"
        lines.append(f"| `{result.case_id}` | `{result.run_id or '-'}` | {score_text} | {passed_text} | {failures_text} |")

    by_run = {run["run_id"]: run for run in runs}
    lines.extend(["", "## 6. Case Details"])
    for idx, result in enumerate(observed_results, 1):
        case = next(item for item in cases if item["id"] == result.case_id)
        run = by_run.get(result.run_id or "")
        lines.extend(_case_detail(idx, case, run, result))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _profile_kit_summary(profile: dict[str, Any]) -> str:
    kits = ((profile.get("_compiled_profile") or {}).get("kits") or [])
    if not kits:
        return "`none`"
    return ", ".join(f"`{kit.get('kit_id')}`" for kit in kits if kit.get("kit_id")) or "`none`"


def _profile_validation_summary(profile: dict[str, Any]) -> str:
    validation = profile.get("profile_validation") or {}
    if not validation:
        return "not checked"
    status = "passed" if validation.get("passed") else "failed"
    warnings = validation.get("warnings") or []
    return f"{status}, warnings={len(warnings)}"


def _case_detail(index: int, case: dict[str, Any], run: dict[str, Any] | None, result: ScoreResult) -> list[str]:
    lines = [
        "",
        f"### 6.{index}. {case['id']} - {case.get('task_type')}",
        "",
        f"- Expected Task: {case.get('user_task')}",
        f"- Matched Run: `{result.run_id or '-'}`",
        "",
        f"#### 6.{index}.1 Dimension Scores",
        "| Dimension | Score |",
        "|---|---:|",
    ]
    for key, value in result.dimension_scores.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", f"#### 6.{index}.2 Failures And Notes", f"- Failures: {', '.join(result.failure_types) or '-'}"])
    for note in result.notes:
        lines.append(f"- {note}")
    if result.deductions:
        lines.extend(
            [
                "",
                f"#### 6.{index}.3 Deductions",
                "```json",
                json.dumps(
                    {
                        "summary": result.deduction_summary,
                        "items": result.deductions,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )
    if result.llm_assistant:
        lines.extend(
            [
                "",
                f"#### 6.{index}.4 LLM Assistant Judge Module",
                "```json",
                json.dumps(result.llm_assistant, ensure_ascii=False, indent=2),
                "```",
            ]
        )
    if run:
        observed = run.get("observed") or {}
        lines.extend(
            [
                "",
                f"#### 6.{index}.4 Observed Execution Path",
                "```json",
                json.dumps(
                    {
                        "user_task_raw": run.get("user_task"),
                        "task_understanding": observed.get("task_understanding"),
                        "plan": observed.get("plan"),
                        "explicit_plan_raw": observed.get("explicit_plan"),
                        "skill_chain": observed.get("skill_chain"),
                        "tool_chain": observed.get("tool_chain"),
                        "tool_results": observed.get("tool_results"),
                        "data_lineage": observed.get("data_lineage"),
                        "tool_args": observed.get("tool_args"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
                f"#### 6.{index}.6 Oracle Evidence",
                "```json",
                json.dumps(_compact_oracle_evidence(observed.get("oracle_evidence") or {}), ensure_ascii=False, indent=2),
                "```",
                "",
                f"#### 6.{index}.7 Claim Evidence Matrix",
                *_claim_evidence_lines(observed),
                "",
                f"#### 6.{index}.8 Final Response",
                "```text",
                (observed.get("final_response") or {}).get("text") or "",
                "```",
            ]
        )
    return lines


def _is_observed_result(result: ScoreResult) -> bool:
    return bool(result.run_id) and "case_not_observed" not in set(result.failure_types)


def _claim_evidence_lines(observed: dict[str, Any]) -> list[str]:
    claims = ((observed.get("final_response") or {}).get("claims") or {})
    items = claims.get("claim_items") or []
    if not items:
        return ["- No structured response claims extracted."]
    lines = [
        "| Claim | Value | Evidence Field | Evidence Value | Status | Note |",
        "|---|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            "| {} | {} | `{}` | {} | {} | {} |".format(
                _escape_table(str(item.get("claim_type") or "-")),
                _escape_table(_clip(str(item.get("value")))),
                _escape_table(str(item.get("evidence_field") or "-")),
                _escape_table(_clip(json.dumps(item.get("evidence_value"), ensure_ascii=False))),
                _escape_table(str(item.get("support_status") or "-")),
                _escape_table(_clip(str(item.get("note") or "-"), limit=80)),
            )
        )
    lines.extend(["", "```json", json.dumps(items, ensure_ascii=False, indent=2), "```"])
    return lines


def _compact_oracle_evidence(oracle: dict[str, Any]) -> dict[str, Any]:
    observed = oracle.get("observed_result_set") or {}
    sample = oracle.get("sample_summary") or {}
    return {
        "evidence_set_count": oracle.get("evidence_set_count", oracle.get("image_result_sets")),
        "evidence_sets": oracle.get("evidence_sets") or [],
        "query_type": oracle.get("query_type"),
        "evidence_stats": oracle.get("evidence_stats") or {},
        "global_summary": oracle.get("global_summary") or {},
        "observed_result_set": {
            "item_count": observed.get("item_count"),
            "pages_observed": observed.get("pages_observed"),
            "items_per_page": observed.get("items_per_page"),
            "score_stats": observed.get("score_stats"),
            "time_stats": observed.get("time_stats"),
            "point_summary": observed.get("point_summary"),
        },
        "sample_summary": {
            "sample_mode": sample.get("sample_mode"),
            "sample_size": sample.get("sample_size"),
            "sample_items": sample.get("sample_items"),
        },
    }


def _experiment_snapshot_lines(runs: list[dict[str, Any]]) -> list[str]:
    if not runs:
        return ["| - | - | - | - |"]
    rows = []
    for run in runs:
        observed = run.get("observed") or {}
        understanding = observed.get("task_understanding") or {}
        tool_args = observed.get("tool_args") or {}
        final_response = observed.get("final_response") or {}
        search_type = (
            understanding.get("intent")
            or tool_args.get("search_type")
            or (observed.get("oracle_evidence") or {}).get("query_type")
            or "-"
        )
        rows.append(
            "| `{}` | {} | {} | {} |".format(
                _escape_table(str(run.get("run_id") or "-")),
                _escape_table(_clip(str(run.get("user_task") or "-"))),
                _escape_table(_clip(str(search_type))),
                _escape_table(_clip(str(final_response.get("text") or "-"))),
            )
        )
    return rows


def _clip(text: str, limit: int = 50) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _escape_table(text: str) -> str:
    return text.replace("|", "\\|")


def _llm_assistant_lines(config: LLMAssistantConfig | None, results: list[ScoreResult]) -> list[str]:
    if not config or not config.enabled:
        return ["- Enabled: false", "- Modules: none", "- Note: Report uses only deterministic rules and profile/case expectations."]
    available = sum(1 for result in results if result.llm_assistant.get("available"))
    summary_available = sum(1 for result in results if ((result.deduction_summary or {}).get("llm_interface") or {}).get("available"))
    return [
        "- Enabled: true",
        "- Modules: `judge`, `summary`",
        f"- Model: `{config.model or '-'}`",
        f"- Base URL: `{config.base_url or '-'}`",
        f"- API Key Env: `{config.api_key_env}`",
        f"- Judge Module Dimensions: `{', '.join(['task_understanding', 'planning'])}`",
        f"- Judge Module Blend: {config.blend:.2f} inside judged dimensions",
        f"- Available Judge Results: {available}/{len(results)}",
        f"- Available Summary Results: {summary_available}/{len(results)}",
        "- Note: LLM Assistant judge is the primary scorer inside the reduced-weight understanding/planning dimensions. Summary is presentation-only. Tool selection, tool order, arguments, data flow, grounding, and safety remain deterministic.",
    ]
