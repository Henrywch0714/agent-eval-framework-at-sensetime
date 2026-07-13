from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .profile import load_profile_cases


ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "datasets"


def load_cases(suite: str, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if profile:
        cases = load_profile_cases(profile, suite)
        if not cases:
            profile_id = profile.get("profile_id") or "unknown"
            raise FileNotFoundError(f"No cases for suite '{suite}' in profile '{profile_id}'")
        return cases
    path = DATASET_DIR / f"suite_{suite}.json"
    if not path.exists():
        raise FileNotFoundError(f"Unknown suite '{suite}'. Missing {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("cases") or []


def match_case_to_run(case: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    matcher = case.get("match") or {}
    any_keywords = matcher.get("any_keywords") or []
    all_keywords = matcher.get("all_keywords") or []
    include_final_text = bool(matcher.get("include_final_text"))
    best_run = None
    best_score = -1
    for run in runs:
        if not _understanding_eligible(case, run):
            continue
        observed = run.get("observed") or {}
        final_text = ((observed.get("final_response") or {}).get("text") or "")
        haystack = str(run.get("user_task") or "")
        if include_final_text:
            haystack = f"{haystack}\n{final_text}"
        if all_keywords and not all(keyword in haystack for keyword in all_keywords):
            continue
        if any_keywords and not any(keyword in haystack for keyword in any_keywords):
            continue
        score = (
            sum(1 for keyword in all_keywords if keyword in haystack)
            + sum(1 for keyword in any_keywords if keyword in haystack)
            + _understanding_match_score(case, run)
        )
        if score > best_score:
            best_run = run
            best_score = score
    return best_run


def _understanding_eligible(case: dict[str, Any], run: dict[str, Any]) -> bool:
    expected = ((case.get("expected") or {}).get("understanding") or {})
    observed = ((run.get("observed") or {}).get("task_understanding") or {})
    if "has_image_input" in expected and observed.get("has_image_input") is not expected.get("has_image_input"):
        return False
    if expected.get("target_type") and observed.get("target_type") not in {expected.get("target_type"), "UNKNOWN"}:
        return False
    if expected.get("target_type_any") and observed.get("target_type") not in set(expected.get("target_type_any") or []) | {"UNKNOWN"}:
        return False
    if expected.get("time_range_days") and observed.get("time_range_days") not in {expected.get("time_range_days"), None}:
        return False
    for key, value in (expected.get("features") or {}).items():
        observed_value = (observed.get("features") or {}).get(key)
        if observed_value not in {value, None}:
            return False
    return True


def _understanding_match_score(case: dict[str, Any], run: dict[str, Any]) -> int:
    expected = ((case.get("expected") or {}).get("understanding") or {})
    observed = ((run.get("observed") or {}).get("task_understanding") or {})
    score = 0
    if "has_image_input" in expected and observed.get("has_image_input") is expected.get("has_image_input"):
        score += 3
    if expected.get("target_type") and observed.get("target_type") == expected.get("target_type"):
        score += 2
    if expected.get("target_type_any") and observed.get("target_type") in expected.get("target_type_any"):
        score += 2
    if expected.get("time_range_days") and observed.get("time_range_days") == expected.get("time_range_days"):
        score += 1
    for key, value in (expected.get("features") or {}).items():
        if (observed.get("features") or {}).get(key) == value:
            score += 1
    return score
