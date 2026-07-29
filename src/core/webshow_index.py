from __future__ import annotations

import json
from pathlib import Path


def build_webshow_index(root: Path) -> Path:
    markdown_dir = root / "reports" / "markdown"
    out_path = root / "reports" / "webshow" / "report_data.js"
    reports = []
    for path in sorted(markdown_dir.glob("*/*_eval_results.jsonl"), reverse=True):
        rows = _observed_rows(_read_jsonl(path))
        if not rows:
            continue
        run_name = path.parent.name
        normalized_runs = _load_normalized_runs(path)
        reports.append(
            {
                "id": run_name,
                "name": f"{run_name} / {path.name}",
                "source": str(path.relative_to(root)),
                "overview": _overview(normalized_runs),
                "cases": rows,
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "window.AGENT_EVAL_REPORTS = "
        + json.dumps(reports, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    return out_path


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _observed_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if _is_observed_case(row)]


def _is_observed_case(row: dict) -> bool:
    failures = row.get("failure_types") or []
    return bool(row.get("run_id")) and "case_not_observed" not in failures


def _load_normalized_runs(results_path: Path) -> list[dict]:
    normalized_path = results_path.with_name(results_path.name.replace("_eval_results.jsonl", "_normalized_runs.jsonl"))
    return _read_jsonl(normalized_path)


def _overview(runs: list[dict]) -> list[dict]:
    out = []
    for run in runs:
        observed = run.get("observed") or {}
        understanding = observed.get("task_understanding") or {}
        tool_args = observed.get("tool_args") or {}
        oracle = observed.get("oracle_evidence") or {}
        final_response = observed.get("final_response") or {}
        out.append(
            {
                "run_id": run.get("run_id"),
                "user_task": _clip(run.get("user_task") or ""),
                "search_type": _clip(str(understanding.get("intent") or tool_args.get("search_type") or oracle.get("query_type") or "")),
                "web_answer": _clip((final_response.get("text") or "")),
            }
        )
    return out


def _clip(text: str, limit: int = 50) -> str:
    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"
