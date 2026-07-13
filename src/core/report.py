from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import ScoreResult
from .scorer import WEIGHTS


def render_report(
    suite: str,
    cases: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    results: list[ScoreResult],
    out_path: Path,
    profile: dict[str, Any] | None = None,
) -> None:
    passed = sum(1 for result in results if result.passed)
    avg = round(sum(result.score for result in results) / len(results), 1) if results else 0
    lines = [
        "# Site Agent Evaluation Report",
        "",
        "## 1. Run Info",
        f"- Generated At: {datetime.now().isoformat(timespec='seconds')}",
        f"- Profile: `{(profile or {}).get('profile_id') or 'default'}`",
        f"- Suite: `{suite}`",
        f"- Cases: {len(cases)}",
        f"- Normalized Runs: {len(runs)}",
        f"- Passed Cases: {passed}",
        f"- Average Score: {avg}",
        "",
        "## 2. Scoring Weights",
        "| Dimension | Weight |",
        "|---|---:|",
    ]
    for key, value in WEIGHTS.items():
        lines.append(f"| {key} | {value:.2f} |")
    lines.extend(["", "## 3. Summary", "| Case | Run | Score | Passed | Failures |", "|---|---|---:|---|---|"])
    for result in results:
        lines.append(f"| `{result.case_id}` | `{result.run_id or '-'}` | {result.score} | {str(result.passed).lower()} | {', '.join(result.failure_types) or '-'} |")

    by_run = {run["run_id"]: run for run in runs}
    lines.extend(["", "## 4. Case Details"])
    for idx, result in enumerate(results, 1):
        case = next(item for item in cases if item["id"] == result.case_id)
        run = by_run.get(result.run_id or "")
        lines.extend(_case_detail(idx, case, run, result))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _case_detail(index: int, case: dict[str, Any], run: dict[str, Any] | None, result: ScoreResult) -> list[str]:
    lines = [
        "",
        f"### 4.{index}. {case['id']} - {case.get('task_type')}",
        "",
        f"- Expected Task: {case.get('user_task')}",
        f"- Matched Run: `{result.run_id or '-'}`",
        "",
        f"#### 4.{index}.1 Dimension Scores",
        "| Dimension | Score |",
        "|---|---:|",
    ]
    for key, value in result.dimension_scores.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", f"#### 4.{index}.2 Failures And Notes", f"- Failures: {', '.join(result.failure_types) or '-'}"])
    for note in result.notes:
        lines.append(f"- {note}")
    if run:
        observed = run.get("observed") or {}
        lines.extend(
            [
                "",
                f"#### 4.{index}.3 Observed Execution Path",
                "```json",
                json.dumps(
                    {
                        "task_understanding": observed.get("task_understanding"),
                        "plan": observed.get("plan"),
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
                f"#### 4.{index}.4 Oracle Evidence",
                "```json",
                json.dumps(observed.get("oracle_evidence"), ensure_ascii=False, indent=2),
                "```",
                "",
                f"#### 4.{index}.5 Final Response",
                "```text",
                (observed.get("final_response") or {}).get("text") or "",
                "```",
            ]
        )
    return lines
