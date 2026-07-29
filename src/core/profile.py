from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .profile_compiler import compile_profile
from .profile_validator import validate_profile


ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / "domain_profiles"
PROFILE_KIT_DIR = ROOT / "profile_kits"


def load_profile(profile_id: str | None) -> dict[str, Any]:
    if not profile_id:
        return {}
    profile_path = PROFILE_DIR / profile_id / "profile.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Unknown profile '{profile_id}'. Missing {profile_path}")
    raw_profile = _read_json(profile_path)
    base = profile_path.parent
    files = raw_profile.get("files") or {}
    sections = {
        "tool_registry": _read_optional_json(base / files.get("tool_registry", "tool_registry.json")),
        "task_reference_set": _read_optional_json(base / files.get("task_reference_set", "task_reference_set.json")),
        "standard_answer_cases": _read_optional_json(base / files.get("standard_answer_cases", "standard_answer_cases.json")),
        "normalizer_map": _read_optional_json(base / files.get("normalizer_map", "normalizer_map.json")),
        "safety_policy": _read_optional_json(base / files.get("safety_policy", "safety_policy.json")),
    }
    profile = compile_profile(raw_profile, sections=sections, kit_dir=PROFILE_KIT_DIR)
    profile["profile_id"] = profile.get("profile_id") or profile_id
    profile["base_dir"] = str(base)
    if profile.get("safety_policy"):
        normalizer_map = dict(profile.get("normalizer_map") or {})
        normalizer_map["safety_policy"] = profile["safety_policy"]
        profile["normalizer_map"] = normalizer_map
    profile["profile_validation"] = validate_profile(profile).as_dict()
    profile["tool_aliases"] = _build_tool_aliases(profile)
    profile["skill_map"] = _build_skill_map(profile)
    return profile


def load_profile_cases(profile: dict[str, Any], suite: str) -> list[dict[str, Any]]:
    payload = profile.get("standard_answer_cases") or {}
    raw_cases = payload.get("cases") or []
    archetypes = payload.get("case_archetypes") or {}
    by_id = {case.get("id"): case for case in raw_cases if case.get("id")}
    selected = []
    for case in raw_cases:
        if not _case_in_suite(case, suite):
            continue
        selected.append(_resolve_case(case, by_id, archetypes))
    return selected


def _case_in_suite(case: dict[str, Any], suite: str) -> bool:
    wanted = suite.lower()
    if wanted == "all":
        return True
    level = str(case.get("level") or "").lower()
    return level == wanted or (wanted == "regression" and level == "regression")


def _resolve_case(
    case: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    archetypes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archetypes = archetypes or {}
    parent_id = case.get("inherits_case")
    current = copy.deepcopy(case)
    if not parent_id:
        return _apply_archetypes(current, archetypes)
    parent = _resolve_case(copy.deepcopy(by_id.get(parent_id) or {}), by_id, archetypes)
    current = _apply_archetypes(current, archetypes)
    merged = _deep_merge(parent, current)
    merged.pop("inherits_case", None)
    return merged


def _apply_archetypes(case: dict[str, Any], archetypes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    refs = case.get("inherits_archetype") or case.get("archetype") or []
    if isinstance(refs, str):
        refs = [refs]
    base: dict[str, Any] = {}
    for ref in refs:
        archetype = archetypes.get(str(ref))
        if isinstance(archetype, dict):
            base = _deep_merge(base, archetype)
    merged = _deep_merge(base, case)
    merged.pop("inherits_archetype", None)
    merged.pop("archetype", None)
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _build_tool_aliases(profile: dict[str, Any]) -> dict[str, str]:
    aliases = dict(((profile.get("normalizer_map") or {}).get("tool_aliases") or {}))
    for tool in ((profile.get("tool_registry") or {}).get("tools") or []):
        name = tool.get("name")
        if not name:
            continue
        aliases[name] = name
        if tool.get("display_name"):
            aliases[tool["display_name"]] = name
        for alias in tool.get("legacy_aliases") or []:
            aliases[alias] = name
    return aliases


def _build_skill_map(profile: dict[str, Any]) -> dict[str, str]:
    normalizer_map = profile.get("normalizer_map") or {}
    skill_map = dict(normalizer_map.get("skill_map") or {})
    category_skill_map = normalizer_map.get("category_skill_map") or {}

    for tool in ((profile.get("tool_registry") or {}).get("tools") or []):
        name = tool.get("name")
        if not name:
            continue
        if tool.get("skill"):
            skill_map[name] = str(tool["skill"])
            continue
        category = tool.get("category")
        if category and category in category_skill_map:
            skill_map[name] = str(category_skill_map[category])
    return skill_map


def _read_optional_json(path: Path) -> dict[str, Any]:
    return _read_json(path) if path.exists() else {}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
