from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SCHEMA_RUN = "site-agent-run-v1"
SCHEMA_TRACE = "site-agent-trace-v1"


@dataclass
class ScoreResult:
    case_id: str
    run_id: str | None
    score: int
    passed: bool
    dimension_scores: dict[str, int]
    failure_types: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    deductions: list[dict[str, Any]] = field(default_factory=list)
    deduction_summary: dict[str, Any] = field(default_factory=dict)
    llm_assistant: dict[str, Any] = field(default_factory=dict)


JsonDict = dict[str, Any]
