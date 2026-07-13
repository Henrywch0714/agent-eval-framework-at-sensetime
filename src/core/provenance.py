from __future__ import annotations

from typing import Any


def attach_provenance(
    tool_chain: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    tool_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach data-lineage edges according to profile tool_registry rules."""
    edges: list[dict[str, Any]] = []
    tool_defs = _tool_defs(tool_registry or {})
    rules = _data_flow_rules(tool_registry or {})
    result_index = _build_result_index(tool_results)

    for call in tool_chain:
        call.setdefault("arg_sources", [])
        for rule in rules:
            if not _rule_targets_call(rule, call, tool_defs):
                continue
            _attach_source_by_rule(call, tool_results, result_index, rule, edges)
    return {"edges": edges, "result_index": list(result_index.values())}


def _data_flow_rules(tool_registry: dict[str, Any]) -> list[dict[str, Any]]:
    return ((tool_registry.get("data_flow_rules") or {}).get("arg_sources") or [])


def _tool_defs(tool_registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in tool_registry.get("tools") or [] if tool.get("name")}


def _rule_targets_call(rule: dict[str, Any], call: dict[str, Any], tool_defs: dict[str, dict[str, Any]]) -> bool:
    tool_name = call.get("tool_name")
    if tool_name in (rule.get("target_tools") or []):
        return True
    categories = set(rule.get("target_tool_categories") or [])
    return bool(categories and (tool_defs.get(str(tool_name)) or {}).get("category") in categories)


def _attach_source_by_rule(
    call: dict[str, Any],
    tool_results: list[dict[str, Any]],
    result_index: dict[str, dict[str, Any]],
    rule: dict[str, Any],
    edges: list[dict[str, Any]],
) -> None:
    args = call.get("args") or {}
    value = _arg_value(args, rule)
    if value in (None, "", [], {}):
        return

    explicit = _explicit_source(args, rule)
    if explicit:
        source = result_index.get(explicit)
        matched = _explicit_source_matches(value, source, rule)
        _add_edge(call, edges, rule, explicit, source, "explicit_key", matched=matched, value=value)
        return

    for result in reversed(_prior_results(tool_results, call.get("order") or 0, rule.get("source_tool"))):
        source = _find_matching_source(value, result, rule, call)
        if source:
            _add_edge(call, edges, rule, source.get("result_key") or result.get("result_key"), source, "inferred_by_value", matched=True, value=value)
            return


def _build_result_index(tool_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for result in tool_results:
        result_key = result.get("result_key")
        if result_key:
            index[str(result_key)] = {
                "result_key": result_key,
                "tool_name": result.get("tool_name"),
                "after_order": result.get("after_order"),
                "output_type": "tool_result",
                **_copy_known_output_fields(result),
            }
        for collection in _child_collections(result):
            for item in result.get(collection) or []:
                key = item.get("result_key")
                if key:
                    index[str(key)] = {
                        "result_key": key,
                        "tool_name": result.get("tool_name"),
                        "after_order": result.get("after_order"),
                        "output_type": _default_output_type(collection),
                        **item,
                    }
    return index


def _child_collections(result: dict[str, Any]) -> list[str]:
    return [key for key, value in result.items() if isinstance(value, list) and all(isinstance(item, dict) for item in value)]


def _default_output_type(collection: str) -> str:
    return collection.rstrip("s") or "child_result"


def _copy_known_output_fields(result: dict[str, Any]) -> dict[str, Any]:
    copied = {}
    for key, value in result.items():
        if key not in {"result_key", "tool_name", "after_order"} and not isinstance(value, (list, dict)):
            copied[key] = result[key]
    return copied


def _arg_value(args: dict[str, Any], rule: dict[str, Any]) -> Any:
    keys = rule.get("target_arg_keys") or []
    if len(keys) > 1:
        if all(_has_arg(args, key) for key in keys):
            return {key: args[key] for key in keys}
        return None
    if len(keys) == 1:
        return args.get(keys[0])
    return None


def _explicit_source(args: dict[str, Any], rule: dict[str, Any]) -> str | None:
    for key in rule.get("explicit_source_keys") or []:
        value = args.get(key)
        if value:
            return str(value)
    return None


def _explicit_source_matches(value: Any, source: dict[str, Any] | None, rule: dict[str, Any]) -> bool:
    if not source:
        return False
    source_value = _source_value(source, rule)
    if source_value in (None, "", [], {}):
        return True
    return _same_value(value, source_value)


def _find_matching_source(
    value: Any,
    result: dict[str, Any],
    rule: dict[str, Any],
    call: dict[str, Any],
) -> dict[str, Any] | None:
    collection = rule.get("source_output_collection")
    if collection:
        for item in result.get(collection) or []:
            source = {"tool_name": result.get("tool_name"), "after_order": result.get("after_order"), **item}
            if not _source_type_allowed(source, rule, call):
                continue
            if _same_value(value, _source_value(source, rule)):
                return source
        return None

    if rule.get("source_result_list_key"):
        source = {"tool_name": result.get("tool_name"), "after_order": result.get("after_order"), **result}
        return source if _same_value(value, _source_value(source, rule)) else None

    if rule.get("source_presence_key") and result.get(rule["source_presence_key"]):
        return {"tool_name": result.get("tool_name"), "after_order": result.get("after_order"), **result}
    return None


def _source_value(source: dict[str, Any], rule: dict[str, Any]) -> Any:
    if rule.get("source_value_key"):
        return source.get(rule["source_value_key"])
    if rule.get("source_result_list_key"):
        return source.get(rule["source_result_list_key"])
    if rule.get("source_presence_key"):
        return bool(source.get(rule["source_presence_key"]))
    return None


def _source_type_allowed(source: dict[str, Any], rule: dict[str, Any], call: dict[str, Any]) -> bool:
    mapping = rule.get("allowed_source_types_by_target_arg") or {}
    source_type_key = rule.get("source_type_key")
    target_type_arg = rule.get("target_type_arg")
    if not mapping or not source_type_key or not target_type_arg:
        return True
    target_value = str((call.get("args") or {}).get(target_type_arg) or "*").upper()
    allowed = mapping.get(target_value) or mapping.get("*") or []
    return source.get(source_type_key) in allowed


def _prior_results(tool_results: list[dict[str, Any]], order: int, tool_name: Any) -> list[dict[str, Any]]:
    return [item for item in tool_results if item.get("tool_name") == tool_name and (item.get("after_order") or 0) < order]


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return all(str(left.get(key)) == str(right.get(key)) for key in left)
    if isinstance(left, list) or isinstance(right, list) or isinstance(left, set) or isinstance(right, set):
        left_set = set(str(item) for item in (left or []))
        right_set = set(str(item) for item in (right or []))
        return bool(left_set) and bool(right_set) and (left_set.issubset(right_set) or bool(left_set & right_set))
    return str(left) == str(right)


def _add_edge(
    call: dict[str, Any],
    edges: list[dict[str, Any]],
    rule: dict[str, Any],
    source_key: Any,
    source: dict[str, Any] | None,
    mode: str,
    matched: bool,
    value: Any,
) -> None:
    edge = {
        "rule_name": rule.get("name"),
        "target_tool_order": call.get("order"),
        "target_tool_name": call.get("tool_name"),
        "arg_name": rule.get("arg_name"),
        "value": value,
        "source_result_key": source_key,
        "source_tool": (source or {}).get("tool_name"),
        "source_output_type": (source or {}).get("output_type") or rule.get("source_output_type"),
        "source_target_type": (source or {}).get(rule.get("source_type_key") or "target_type"),
        "mode": mode,
        "matched": matched,
    }
    call.setdefault("arg_sources", []).append(edge)
    edges.append(edge)


def _has_arg(args: dict[str, Any], key: str) -> bool:
    value = args.get(key)
    return value is not None and value != "" and value != []
