from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .log_adapter import extract_tool_items
from .normalizer import load_jsonl


DEFAULT_ERROR_MARKERS = [
    "data error",
    "dataerror",
    "data_error",
    "database error",
    "数据库错误",
    "数据库异常",
    "数据库报错",
    "数据错误",
    "数据异常",
]


@dataclass
class DataErrorFinding:
    line: int
    reason: str
    snippet: str


@dataclass
class DataErrorCheck:
    has_error: bool
    findings: list[DataErrorFinding]

    def summary(self, limit: int = 3) -> str:
        parts = [f"line {item.line}: {item.reason} ({item.snippet})" for item in self.findings[:limit]]
        if len(self.findings) > limit:
            parts.append(f"... +{len(self.findings) - limit} more")
        return "; ".join(parts)


class AgentDataError(RuntimeError):
    def __init__(self, check: DataErrorCheck) -> None:
        super().__init__(check.summary())
        self.check = check


def check_agent_data_errors(events_path: Path, profile: dict[str, Any] | None = None) -> DataErrorCheck:
    """Detect runtime/data-source failures before creating evaluation reports.

    This gate is intentionally narrower than normal scoring failures. It only
    catches cases where the Agent runtime or backing data service reports an
    error, because those runs are not meaningful Agent capability samples.
    """
    profile = profile or {}
    config = profile.get("normalizer_map") or {}
    markers = _error_markers(config)
    findings: list[DataErrorFinding] = []
    for line_no, event in enumerate(load_jsonl(events_path), 1):
        findings.extend(_event_findings(line_no, event, markers, config))
    return DataErrorCheck(has_error=bool(findings), findings=findings)


def filter_evaluable_runs(
    runs: list[dict[str, Any]],
    trace: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], DataErrorCheck]:
    """Drop only unrecovered data-error runs, while keeping later clean runs.

    A run is treated as recovered when it has a final answer or useful tool
    calls after an earlier runtime error, which happens when the user refreshes
    or retries a question inside the same browser session.
    """
    skipped_ids = set()
    findings: list[DataErrorFinding] = []
    for run in runs:
        runtime_errors = ((run.get("observed") or {}).get("runtime_errors") or [])
        if runtime_errors and not _has_evaluable_output(run):
            run_id = str(run.get("run_id") or "-")
            skipped_ids.add(run_id)
            first = runtime_errors[0] if isinstance(runtime_errors[0], dict) else {"snippet": runtime_errors[0]}
            findings.append(
                DataErrorFinding(
                    line=0,
                    reason=f"unrecovered_run_data_error:{run_id}",
                    snippet=str(first.get("snippet") or "")[:220],
                )
            )
    kept_runs = [run for run in runs if run.get("run_id") not in skipped_ids]
    kept_trace = [event for event in trace if event.get("run_id") not in skipped_ids]
    return kept_runs, kept_trace, DataErrorCheck(has_error=bool(findings), findings=findings)


def _has_evaluable_output(run: dict[str, Any]) -> bool:
    observed = run.get("observed") or {}
    final_text = ((observed.get("final_response") or {}).get("text") or "").strip()
    return bool(final_text)


def _error_markers(config: dict[str, Any]) -> list[str]:
    configured = ((config.get("error_gate") or {}).get("data_error_markers") or [])
    return [str(item).lower() for item in (configured or DEFAULT_ERROR_MARKERS)]


def _event_findings(line_no: int, event: dict[str, Any], markers: list[str], config: dict[str, Any]) -> list[DataErrorFinding]:
    findings = []
    data = event.get("data")
    if isinstance(data, dict) and data.get("error") not in (None, "", [], {}):
        findings.append(_finding(line_no, "sse_error", data.get("error")))

    for response in _function_responses(data, config):
        status = str(response.get("status") or "").lower()
        if status in {"error", "failed", "failure"}:
            findings.append(_finding(line_no, "tool_response_error_status", response))
        explicit_error = response.get("error") or response.get("error_message") or response.get("message")
        if explicit_error and _contains_marker(explicit_error, markers):
            findings.append(_finding(line_no, "tool_response_data_error", explicit_error))
        payload = response.get("data")
        if _contains_marker(payload, markers):
            findings.append(_finding(line_no, "tool_response_data_error", payload))

    if _contains_marker(data, markers):
        findings.append(_finding(line_no, "agent_data_error_marker", data))
    return findings


def _function_responses(value: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    responses = []
    for item in extract_tool_items([value], tool_aliases=None, config=config) if isinstance(value, dict) else []:
        if item.get("kind") == "result" and isinstance(item.get("response"), dict):
            responses.append(item["response"])
    return responses


def _contains_marker(value: Any, markers: list[str]) -> bool:
    if value in (None, "", [], {}):
        return False
    text = _compact(value).lower()
    return any(marker in text for marker in markers)


def _finding(line_no: int, reason: str, value: Any) -> DataErrorFinding:
    return DataErrorFinding(line=line_no, reason=reason, snippet=_compact(value)[:220])


def _compact(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)
