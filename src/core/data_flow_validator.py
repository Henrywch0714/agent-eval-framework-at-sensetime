from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataFlowValidation:
    score: int
    failure_types: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def validate_data_flow(
    run: dict[str, Any],
    case: dict[str, Any],
    tool_registry: dict[str, Any] | None = None,
) -> DataFlowValidation:
    observed = run.get("observed") or {}
    tool_chain = observed.get("tool_chain") or []
    tool_results = observed.get("tool_results") or []
    rules = _data_flow_rules(tool_registry or {})
    tool_defs = _tool_defs(tool_registry or {})
    checks: list[bool] = []
    failures: list[str] = []
    notes: list[str] = []

    for rule in rules:
        _validate_rule(rule, case, tool_chain, tool_results, tool_defs, checks, failures, notes)

    if not checks:
        return DataFlowValidation(score=100)
    score = round(100 * sum(1 for item in checks if item) / len(checks))
    return DataFlowValidation(score=score, failure_types=sorted(set(failures)), notes=notes)


def _validate_rule(
    rule: dict[str, Any],
    case: dict[str, Any],
    tool_chain: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    tool_defs: dict[str, dict[str, Any]],
    checks: list[bool],
    failures: list[str],
    notes: list[str],
) -> None:
    if not _rule_required_for_case(rule, case):
        return
    for call in tool_chain:
        if not _rule_targets_call(rule, call, tool_defs):
            continue
        args = call.get("args") or {}
        if rule.get("target_required_when_arg_present") and not _has_arg(args, rule["target_required_when_arg_present"]):
            continue
        if rule.get("required_when_source_in_expected_order") and rule.get("source_tool") not in _expected_order(case):
            continue

        value = _arg_value(args, rule)
        source = _arg_source(call, rule.get("arg_name"))
        if source:
            ok = (
                source.get("matched") is True
                and source.get("source_tool") == rule.get("source_tool")
                and _source_type_allowed(source, rule, call)
            )
            _record(ok, rule, f"{call.get('tool_name')}: {rule.get('arg_name')} 来源 {source.get('source_result_key')} 不符合规则 {rule.get('name')}", checks, failures, notes)
            continue

        ok = _fallback_rule_match(value, call, tool_results, rule)
        _record(ok, rule, f"{call.get('tool_name')}: {rule.get('arg_name')} 未能证明来自 {rule.get('source_tool')}，规则 {rule.get('name')}", checks, failures, notes)


def _rule_required_for_case(rule: dict[str, Any], case: dict[str, Any]) -> bool:
    if rule.get("required_when_source_in_expected_order"):
        return rule.get("source_tool") in _expected_order(case)
    flags = rule.get("case_required_flags") or []
    if flags:
        return _case_requires_arg_flow(case, rule.get("arg_name"), flags)
    return True


def _case_requires_arg_flow(case: dict[str, Any], arg_name: Any, flags: list[str]) -> bool:
    arguments = ((case.get("expected") or {}).get("arguments") or {})
    for rules in arguments.values():
        if not isinstance(rules, dict):
            continue
        if any(rules.get(flag) for flag in flags):
            return True
        if arg_name in (rules.get("required_args") or []):
            return True
    return False


def _fallback_rule_match(value: Any, call: dict[str, Any], tool_results: list[dict[str, Any]], rule: dict[str, Any]) -> bool:
    if value in (None, "", [], {}):
        return False
    for result in reversed(_prior_results(tool_results, call.get("order") or 0, rule.get("source_tool"))):
        if _result_matches_value(value, result, rule, call):
            return True
    return False


def _result_matches_value(value: Any, result: dict[str, Any], rule: dict[str, Any], call: dict[str, Any]) -> bool:
    collection = rule.get("source_output_collection")
    if collection:
        for item in result.get(collection) or []:
            source = {"tool_name": result.get("tool_name"), "after_order": result.get("after_order"), **item}
            if _source_type_allowed(source, rule, call) and _same_value(value, _source_value(source, rule)):
                return True
        return False
    if rule.get("source_result_list_key"):
        return _same_value(value, result.get(rule["source_result_list_key"]))
    if rule.get("source_presence_key"):
        return bool(result.get(rule["source_presence_key"])) and value not in (None, "", [], {})
    return False


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


def _arg_value(args: dict[str, Any], rule: dict[str, Any]) -> Any:
    keys = rule.get("target_arg_keys") or []
    if len(keys) > 1:
        if all(_has_arg(args, key) for key in keys):
            return {key: args[key] for key in keys}
        return None
    if len(keys) == 1:
        return args.get(keys[0])
    return None


def _arg_source(call: dict[str, Any], arg_name: Any) -> dict[str, Any] | None:
    for source in call.get("arg_sources") or []:
        if source.get("arg_name") == arg_name:
            return source
    return None


def _source_type_allowed(source: dict[str, Any], rule: dict[str, Any], call: dict[str, Any]) -> bool:
    mapping = rule.get("allowed_source_types_by_target_arg") or {}
    source_type_key = rule.get("source_type_key")
    target_type_arg = rule.get("target_type_arg")
    if not mapping or not source_type_key or not target_type_arg:
        return True
    target_value = str((call.get("args") or {}).get(target_type_arg) or "*").upper()
    allowed = mapping.get(target_value) or mapping.get("*") or []
    return source.get("source_target_type") in allowed or source.get(source_type_key) in allowed


def _source_value(source: dict[str, Any], rule: dict[str, Any]) -> Any:
    if rule.get("source_value_key"):
        return source.get(rule["source_value_key"])
    if rule.get("source_result_list_key"):
        return source.get(rule["source_result_list_key"])
    if rule.get("source_presence_key"):
        return bool(source.get(rule["source_presence_key"]))
    return None


def _prior_results(tool_results: list[dict[str, Any]], order: int, tool_name: Any) -> list[dict[str, Any]]:
    return [item for item in tool_results if item.get("tool_name") == tool_name and (item.get("after_order") or 0) < order]


def _expected_order(case: dict[str, Any]) -> list[str]:
    return (((case.get("expected") or {}).get("tool_plan") or {}).get("expected_order") or [])


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return all(str(left.get(key)) == str(right.get(key)) for key in left)
    if isinstance(left, list) or isinstance(right, list) or isinstance(left, set) or isinstance(right, set):
        left_set = set(str(item) for item in (left or []))
        right_set = set(str(item) for item in (right or []))
        return bool(left_set) and bool(right_set) and (left_set.issubset(right_set) or bool(left_set & right_set))
    return str(left) == str(right)


def _has_arg(args: dict[str, Any], key: str) -> bool:
    value = args.get(key)
    return value is not None and value != "" and value != []


def _record(ok: bool, rule: dict[str, Any], message: str, checks: list[bool], failures: list[str], notes: list[str]) -> None:
    checks.append(ok)
    if not ok:
        failures.append(rule.get("failure_type") or "data_flow_rule_failed")
        notes.append(f"[DATA FLOW] {message}")
