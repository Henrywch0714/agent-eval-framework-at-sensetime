from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


SECTION_KEYS = {
    "capture",
    "normalizer_map",
    "safety_policy",
    "scoring_policy",
    "tool_registry",
    "task_reference_set",
    "standard_answer_cases",
}

UNIQUE_LIST_KEYS = {
    "image_args",
    "image_markers",
    "point_tools",
    "point_markers",
    "text_search_markers",
    "date_arg_names",
    "keep_args",
    "redact_args",
    "meta_tools",
}


def compile_profile(
    profile: dict[str, Any],
    sections: dict[str, dict[str, Any]] | None = None,
    kit_dir: Path | None = None,
) -> dict[str, Any]:
    """Compile a domain profile from reusable kits plus local overrides.

    Kits are defaults. The concrete domain profile and its local JSON files
    always win, which keeps existing profiles backwards compatible.
    """
    sections = sections or {}
    compiled: dict[str, Any] = {}
    used_kits = []

    for kit_ref in _kit_refs(profile):
        kit = _load_kit(kit_ref, kit_dir)
        compiled = _deep_merge(compiled, _kit_payload(kit))
        used_kits.append(
            {
                "kit_id": kit.get("kit_id") or kit_ref,
                "version": kit.get("version") or "",
                "description": kit.get("description") or "",
            }
        )

    compiled = _deep_merge(compiled, profile)
    for key, value in sections.items():
        if value:
            compiled[key] = _deep_merge(compiled.get(key) or {}, value)

    compiled = _expand_tool_registry(compiled)
    compiled["_compiled_profile"] = {
        "enabled": bool(used_kits),
        "kits": used_kits,
        "local_sections": sorted(key for key, value in sections.items() if value),
    }
    return compiled


def _kit_refs(profile: dict[str, Any]) -> list[str]:
    refs = profile.get("uses") or profile.get("profile_kits") or []
    if isinstance(refs, str):
        return [refs]
    return [str(item) for item in refs if item]


def _load_kit(kit_ref: str, kit_dir: Path | None) -> dict[str, Any]:
    if kit_dir is None:
        raise FileNotFoundError(f"Profile kit directory is not configured: {kit_ref}")
    path = kit_dir / kit_ref / "kit.json"
    if not path.exists():
        raise FileNotFoundError(f"Unknown profile kit '{kit_ref}'. Missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _kit_payload(kit: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if isinstance(kit.get("profile_defaults"), dict):
        payload = _deep_merge(payload, kit["profile_defaults"])
    for key in SECTION_KEYS:
        if isinstance(kit.get(key), dict):
            payload[key] = _deep_merge(payload.get(key) or {}, kit[key])
    return payload


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in {"tools", "cases"} and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_named_list(out[key], value, "name" if key == "tools" else "id")
        elif key == "arg_sources" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_named_list(out[key], value, "name")
        elif key == "claim_extractors" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_claim_rules(out[key], value, ["claim_type", "output_key"])
        elif key == "grounding_rules" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_claim_rules(out[key], value, ["claim_type", "claim_key"])
        elif key == "boolean_flags" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_claim_rules(out[key], value, ["output_key", "claim_type"])
        elif key == "time_range_markers" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_claim_rules(out[key], value, ["days"])
        elif key == "target_type_text_keywords" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_claim_rules(out[key], value, ["target_type"])
        elif key in UNIQUE_LIST_KEYS and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_unique_list(out[key], value)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _expand_tool_registry(profile: dict[str, Any]) -> dict[str, Any]:
    registry = profile.get("tool_registry")
    if not isinstance(registry, dict):
        return profile
    fragments = registry.get("arg_schema_fragments") or {}
    archetypes = registry.get("tool_archetypes") or {}
    tools = registry.get("tools") or []
    if not isinstance(archetypes, dict) or not isinstance(tools, list):
        return profile

    expanded_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            expanded_tools.append(tool)
            continue
        base: dict[str, Any] = {}
        for ref in _as_list(tool.get("extends")):
            archetype = archetypes.get(ref)
            if isinstance(archetype, dict):
                base = _deep_merge(base, _expand_arg_fragments(archetype, fragments))
        merged = _deep_merge(base, _expand_arg_fragments(tool, fragments))
        merged.pop("extends", None)
        merged.pop("arg_fragments", None)
        expanded_tools.append(merged)

    out = copy.deepcopy(profile)
    out_registry = copy.deepcopy(registry)
    out_registry["tools"] = expanded_tools
    out["tool_registry"] = out_registry
    return out


def _expand_arg_fragments(item: dict[str, Any], fragments: dict[str, Any]) -> dict[str, Any]:
    expanded = copy.deepcopy(item)
    local_args = copy.deepcopy(expanded.get("args") or {})
    args: dict[str, Any] = {}
    for ref in _as_list(expanded.get("arg_fragments")):
        fragment_args = fragments.get(ref)
        if isinstance(fragment_args, dict):
            args = _deep_merge(args, fragment_args)
    args = _deep_merge(args, local_args)
    if args:
        expanded["args"] = args
    return expanded


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)]


def _merge_named_list(base: list[Any], override: list[Any], key: str) -> list[Any]:
    out = copy.deepcopy(base)
    index = {item.get(key): pos for pos, item in enumerate(out) if isinstance(item, dict) and item.get(key)}
    for item in override:
        if not isinstance(item, dict) or not item.get(key):
            out.append(copy.deepcopy(item))
            continue
        item_key = item[key]
        if item_key in index:
            out[index[item_key]] = _deep_merge(out[index[item_key]], item)
        else:
            index[item_key] = len(out)
            out.append(copy.deepcopy(item))
    return out


def _merge_claim_rules(base: list[Any], override: list[Any], keys: list[str]) -> list[Any]:
    out = copy.deepcopy(base)
    index = {_first_present_key(item, keys): pos for pos, item in enumerate(out) if isinstance(item, dict) and _first_present_key(item, keys)}
    for item in override:
        if not isinstance(item, dict):
            out.append(copy.deepcopy(item))
            continue
        item_key = _first_present_key(item, keys)
        if item_key and item_key in index:
            out[index[item_key]] = _deep_merge(out[index[item_key]], item)
        else:
            if item_key:
                index[item_key] = len(out)
            out.append(copy.deepcopy(item))
    return out


def _merge_unique_list(base: list[Any], override: list[Any]) -> list[Any]:
    out = copy.deepcopy(base)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in out}
    for item in override:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(copy.deepcopy(item))
    return out


def _first_present_key(item: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        if item.get(key):
            return str(item[key])
    return ""
