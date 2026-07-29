from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProfileValidation:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "errors": self.errors, "warnings": self.warnings}


class ProfileValidationError(ValueError):
    def __init__(self, validation: ProfileValidation):
        super().__init__("Invalid profile: " + "; ".join(validation.errors))
        self.validation = validation


def validate_profile(profile: dict[str, Any]) -> ProfileValidation:
    errors: list[str] = []
    warnings: list[str] = []

    if not profile.get("profile_id"):
        errors.append("profile_id is required")

    tool_registry = profile.get("tool_registry") or {}
    normalizer_map = profile.get("normalizer_map") or {}
    cases = (profile.get("standard_answer_cases") or {}).get("cases") or []
    tools = tool_registry.get("tools") or []
    tool_names = _validate_tools(tools, errors)
    meta_tools = _meta_tools(normalizer_map)
    _validate_aliases(normalizer_map, tool_names, meta_tools, warnings)
    _validate_log_adapter(normalizer_map.get("log_adapter") or {}, warnings)
    _validate_evidence_aggregator(normalizer_map.get("evidence_aggregator") or {}, warnings)
    _validate_response_claim_rules(normalizer_map, warnings)
    _validate_safety_policy(profile.get("safety_policy") or normalizer_map.get("safety_policy") or {}, warnings)
    _validate_scoring_policy(profile.get("scoring_policy") or {}, warnings)
    _validate_cases(cases, tool_names, meta_tools, warnings)
    _validate_data_flow_rules(tool_registry, tool_names, warnings)

    validation = ProfileValidation(passed=not errors, errors=errors, warnings=warnings)
    if errors:
        raise ProfileValidationError(validation)
    return validation


def _validate_tools(tools: list[Any], errors: list[str]) -> set[str]:
    names: set[str] = set()
    for index, tool in enumerate(tools, 1):
        if not isinstance(tool, dict):
            errors.append(f"tool_registry.tools[{index}] must be an object")
            continue
        name = tool.get("name")
        if not name:
            errors.append(f"tool_registry.tools[{index}] missing name")
            continue
        if name in names:
            errors.append(f"duplicate tool name: {name}")
        names.add(str(name))
    return names


def _meta_tools(normalizer_map: dict[str, Any]) -> set[str]:
    configured = normalizer_map.get("meta_tools") or []
    return {"update_plan", "request_user_input", "load_skill", *[str(item) for item in configured if item]}


def _validate_aliases(normalizer_map: dict[str, Any], tool_names: set[str], meta_tools: set[str], warnings: list[str]) -> None:
    aliases = normalizer_map.get("tool_aliases") or {}
    if not isinstance(aliases, dict):
        warnings.append("normalizer_map.tool_aliases should be an object")
        return
    for alias, target in aliases.items():
        if tool_names and target not in tool_names and target not in meta_tools:
            warnings.append(f"tool alias {alias!r} points to unknown tool {target!r}")


def _validate_log_adapter(adapter: dict[str, Any], warnings: list[str]) -> None:
    if not adapter:
        return
    if not adapter.get("event_kind_field"):
        warnings.append("normalizer_map.log_adapter.event_kind_field is recommended")
    for key in ["message_event_kinds", "close_event_kinds"]:
        if key in adapter and not isinstance(adapter.get(key), list):
            warnings.append(f"normalizer_map.log_adapter.{key} should be a list")
    request_text = adapter.get("request_text") or {}
    if request_text and not isinstance(request_text.get("parts_path", []), list):
        warnings.append("normalizer_map.log_adapter.request_text.parts_path should be a list")
    tool_trace = adapter.get("tool_trace") or {}
    if tool_trace:
        if not isinstance(tool_trace.get("parts_path", []), list):
            warnings.append("normalizer_map.log_adapter.tool_trace.parts_path should be a list")
        for key in ["call_key", "result_key", "name_key"]:
            if not tool_trace.get(key):
                warnings.append(f"normalizer_map.log_adapter.tool_trace.{key} is recommended")


def _validate_evidence_aggregator(aggregator: dict[str, Any], warnings: list[str]) -> None:
    if not aggregator:
        return
    collectors = aggregator.get("collectors") or []
    if collectors and not isinstance(collectors, list):
        warnings.append("normalizer_map.evidence_aggregator.collectors should be a list")
        return
    for index, collector in enumerate(collectors, 1):
        if not isinstance(collector, dict):
            warnings.append(f"normalizer_map.evidence_aggregator.collectors[{index}] should be an object")
            continue
        if collector.get("result_list_path") is not None and not isinstance(collector.get("result_list_path"), list):
            warnings.append(f"normalizer_map.evidence_aggregator.collectors[{index}].result_list_path should be a list")
        if collector.get("page_path") is not None and not isinstance(collector.get("page_path"), list):
            warnings.append(f"normalizer_map.evidence_aggregator.collectors[{index}].page_path should be a list")
    item_fields = aggregator.get("item_fields") or {}
    if item_fields and not isinstance(item_fields, dict):
        warnings.append("normalizer_map.evidence_aggregator.item_fields should be an object")
    sample = aggregator.get("sample") or {}
    if sample and not isinstance(sample, dict):
        warnings.append("normalizer_map.evidence_aggregator.sample should be an object")


def _validate_cases(cases: list[Any], tool_names: set[str], meta_tools: set[str], warnings: list[str]) -> None:
    case_ids: set[str] = set()
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            warnings.append(f"standard_answer_cases.cases[{index}] should be an object")
            continue
        case_id = case.get("id")
        if not case_id:
            warnings.append(f"standard_answer_cases.cases[{index}] missing id")
        elif case_id in case_ids:
            warnings.append(f"duplicate case id: {case_id}")
        case_ids.add(str(case_id))
        expected = case.get("expected") or {}
        plan = expected.get("tool_plan") or expected.get("tool_chain") or {}
        for key in ["must_include", "must_not_include", "expected_order", "expected_order_prefix"]:
            for tool in plan.get(key) or []:
                if tool_names and tool not in tool_names and tool not in meta_tools:
                    warnings.append(f"case {case_id or index} references unknown tool {tool!r} in {key}")
        arguments = expected.get("arguments") or expected.get("tool_args") or {}
        for tool in arguments:
            if tool_names and tool not in tool_names:
                warnings.append(f"case {case_id or index} has argument rules for unknown tool {tool!r}")


def _validate_data_flow_rules(tool_registry: dict[str, Any], tool_names: set[str], warnings: list[str]) -> None:
    rules = ((tool_registry.get("data_flow_rules") or {}).get("arg_sources") or [])
    categories = {str(tool.get("category")) for tool in tool_registry.get("tools") or [] if isinstance(tool, dict) and tool.get("category")}
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            warnings.append(f"data_flow_rules.arg_sources[{index}] should be an object")
            continue
        if not rule.get("name"):
            warnings.append(f"data_flow_rules.arg_sources[{index}] missing name")
        source_tool = rule.get("source_tool")
        if source_tool and tool_names and source_tool not in tool_names and source_tool != "get_tool_result":
            warnings.append(f"data flow rule {rule.get('name') or index} source_tool {source_tool!r} is unknown")
        for tool in rule.get("target_tools") or []:
            if tool_names and tool not in tool_names:
                warnings.append(f"data flow rule {rule.get('name') or index} target tool {tool!r} is unknown")
        for category in rule.get("target_tool_categories") or []:
            if categories and category not in categories:
                warnings.append(f"data flow rule {rule.get('name') or index} target category {category!r} is unknown")


def _validate_response_claim_rules(normalizer_map: dict[str, Any], warnings: list[str]) -> None:
    rules = (normalizer_map.get("response_claims") or {})
    allowed_methods = {"number_after_prefix", "number_before_suffix", "number_near_anchor", "pattern_exists", "contains_any", "regex_first_int"}
    allowed_comparators = {"equals", "evidence_gte_threshold", "gte", "lte", "must_not_exist"}
    for index, item in enumerate(rules.get("boolean_flags") or [], 1):
        if not isinstance(item, dict):
            warnings.append(f"response_claims.boolean_flags[{index}] should be an object")
            continue
        if not item.get("output_key"):
            warnings.append(f"response_claims.boolean_flags[{index}] missing output_key")
        method = item.get("method")
        if method and method not in allowed_methods:
            warnings.append(f"response_claims.boolean_flags[{index}] unknown method {method!r}")
    for index, item in enumerate(rules.get("claim_extractors") or [], 1):
        if not isinstance(item, dict):
            warnings.append(f"response_claims.claim_extractors[{index}] should be an object")
            continue
        if not item.get("claim_type"):
            warnings.append(f"response_claims.claim_extractors[{index}] missing claim_type")
        method = item.get("method")
        if method not in allowed_methods:
            warnings.append(f"response_claims.claim_extractors[{index}] unknown method {method!r}")
        comparator = item.get("comparator")
        if comparator and comparator not in allowed_comparators:
            warnings.append(f"response_claims.claim_extractors[{index}] unknown comparator {comparator!r}")
    for index, item in enumerate(rules.get("grounding_rules") or [], 1):
        if not isinstance(item, dict):
            warnings.append(f"response_claims.grounding_rules[{index}] should be an object")
            continue
        if not item.get("claim_type") and not item.get("claim_key"):
            warnings.append(f"response_claims.grounding_rules[{index}] missing claim_type or claim_key")
        comparator = item.get("comparator")
        if comparator not in allowed_comparators:
            warnings.append(f"response_claims.grounding_rules[{index}] unknown comparator {comparator!r}")


def _validate_safety_policy(policy: dict[str, Any], warnings: list[str]) -> None:
    if not policy:
        return
    allowed_methods = {"contains_any", "contains_all", "pattern_exists"}
    allowed_scopes = {"task", "response", "combined", "task_and_response", ""}
    for section in ["risk_detectors", "response_flags"]:
        for index, item in enumerate(policy.get(section) or [], 1):
            if not isinstance(item, dict):
                warnings.append(f"safety_policy.{section}[{index}] should be an object")
                continue
            if not item.get("flag") and not item.get("name"):
                warnings.append(f"safety_policy.{section}[{index}] missing flag")
            method = item.get("method") or "contains_any"
            if method not in allowed_methods:
                warnings.append(f"safety_policy.{section}[{index}] unknown method {method!r}")
            scope = item.get("scope") or ""
            if scope not in allowed_scopes:
                warnings.append(f"safety_policy.{section}[{index}] unknown scope {scope!r}")
    for index, item in enumerate(policy.get("regex_flags") or [], 1):
        if not isinstance(item, dict):
            warnings.append(f"safety_policy.regex_flags[{index}] should be an object")
            continue
        if not item.get("flag") and not item.get("name"):
            warnings.append(f"safety_policy.regex_flags[{index}] missing flag")
        if not item.get("pattern"):
            warnings.append(f"safety_policy.regex_flags[{index}] missing pattern")
    for index, item in enumerate(policy.get("expected_behavior_checks") or [], 1):
        if not isinstance(item, dict):
            warnings.append(f"safety_policy.expected_behavior_checks[{index}] should be an object")
            continue
        if not item.get("case_key"):
            warnings.append(f"safety_policy.expected_behavior_checks[{index}] missing case_key")
        if not item.get("requires_any_flag") and not item.get("requires_all_flags") and not item.get("requires_all_flag"):
            warnings.append(f"safety_policy.expected_behavior_checks[{index}] has no required flags")


def _validate_scoring_policy(policy: dict[str, Any], warnings: list[str]) -> None:
    if not policy:
        return
    supported = {
        "task_understanding",
        "planning",
        "skill_selection",
        "tool_selection",
        "tool_order",
        "tool_arguments",
        "data_flow",
        "evidence_grounding",
        "response_quality",
        "safety",
    }
    weights = policy.get("weights") or {}
    if not isinstance(weights, dict):
        warnings.append("scoring_policy.weights should be an object")
        return
    total = 0.0
    for key, value in weights.items():
        if key not in supported:
            warnings.append(f"scoring_policy.weights references unsupported dimension {key!r}")
        try:
            total += float(value)
        except (TypeError, ValueError):
            warnings.append(f"scoring_policy.weights[{key!r}] should be numeric")
    if weights and total <= 0:
        warnings.append("scoring_policy.weights total should be greater than 0")
    try:
        threshold = int(policy.get("pass_threshold", 70))
        if threshold < 0 or threshold > 100:
            warnings.append("scoring_policy.pass_threshold should be between 0 and 100")
    except (TypeError, ValueError):
        warnings.append("scoring_policy.pass_threshold should be an integer")
