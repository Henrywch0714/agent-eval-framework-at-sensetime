from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


META_TOOLS = {"update_plan", "request_user_input", "load_skill"}


@dataclass
class ToolSchemaValidation:
    score: int
    failure_types: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def validate_tool_schema(run: dict[str, Any], tool_registry: dict[str, Any] | None, case: dict[str, Any] | None = None) -> ToolSchemaValidation:
    registry = _registry_by_name(tool_registry or {})
    if not registry:
        return ToolSchemaValidation(score=100)

    checks: list[bool] = []
    failures: list[str] = []
    notes: list[str] = []
    for item in ((run.get("observed") or {}).get("tool_chain") or []):
        tool_name = item.get("tool_name")
        if not tool_name or tool_name in META_TOOLS:
            continue
        tool_def = registry.get(tool_name)
        if not tool_def:
            checks.append(False)
            failures.append("unknown_tool")
            notes.append(f"[TOOL SCHEMA] 未在 tool_registry 中找到工具定义：{tool_name}")
            continue
        args = item.get("args") or {}
        _validate_tool_call(tool_name, args, tool_def, case or {}, checks, failures, notes)

    if not checks:
        return ToolSchemaValidation(score=100, failure_types=sorted(set(failures)), notes=notes)
    score = round(100 * sum(1 for item in checks if item) / len(checks))
    return ToolSchemaValidation(score=score, failure_types=sorted(set(failures)), notes=notes)


def _validate_tool_call(
    tool_name: str,
    args: dict[str, Any],
    tool_def: dict[str, Any],
    case: dict[str, Any],
    checks: list[bool],
    failures: list[str],
    notes: list[str],
) -> None:
    rules = tool_def.get("argument_rules") or {}
    arg_defs = tool_def.get("args") or {}

    for arg in rules.get("required") or []:
        _record(_has_arg(args, arg), "missing_required_arg", f"{tool_name}: 缺少必填参数 {arg}", checks, failures, notes)

    for group in rules.get("required_one_of") or []:
        present = [arg for arg in group if _has_arg(args, arg)]
        _record(bool(present), "missing_required_one_of", f"{tool_name}: 需要至少提供其中一个参数 {group}", checks, failures, notes)

    for group in rules.get("exactly_one_of") or []:
        present = [arg for arg in group if _has_arg(args, arg)]
        _record(len(present) == 1, "invalid_exactly_one_of", f"{tool_name}: 参数组 {group} 必须且只能提供一个，实际提供 {present}", checks, failures, notes)

    for group in rules.get("mutually_exclusive_groups") or []:
        present = [arg for arg in group if _has_arg(args, arg)]
        _record(len(present) <= 1, "mutually_exclusive_args", f"{tool_name}: 参数互斥，不能同时提供 {present}", checks, failures, notes)

    for base_arg, exclusive_args in (rules.get("mutually_exclusive_with") or {}).items():
        if not _has_arg(args, base_arg):
            continue
        present = [arg for arg in exclusive_args if _has_arg(args, arg)]
        _record(not present, "mutually_exclusive_args", f"{tool_name}: {base_arg} 不能和 {present} 同时提供", checks, failures, notes)

    for pair in rules.get("requires_together") or []:
        present = [arg for arg in pair if _has_arg(args, arg)]
        _record(len(present) in {0, len(pair)}, "missing_required_pair", f"{tool_name}: 参数 {pair} 必须成组提供，实际提供 {present}", checks, failures, notes)

    for rule in rules.get("required_when") or []:
        trigger = rule.get("when_arg_present")
        if trigger and _has_arg(args, trigger):
            for arg in rule.get("required") or []:
                _record(_has_arg(args, arg), "missing_required_when", f"{tool_name}: 提供 {trigger} 时必须同时提供 {arg}", checks, failures, notes)

    for arg, allowed in (rules.get("allowed_values") or {}).items():
        if _has_arg(args, arg):
            _record(args.get(arg) in allowed, "invalid_allowed_value", f"{tool_name}: {arg}={args.get(arg)!r} 不在允许值 {allowed} 中", checks, failures, notes)

    for arg, arg_def in arg_defs.items():
        if not _has_arg(args, arg):
            continue
        allowed = arg_def.get("allowed") if isinstance(arg_def, dict) else None
        if allowed:
            _record(args.get(arg) in allowed, "invalid_allowed_value", f"{tool_name}: {arg}={args.get(arg)!r} 不在允许值 {allowed} 中", checks, failures, notes)
        fmt = arg_def.get("format") if isinstance(arg_def, dict) else None
        if fmt:
            _record(_matches_format(str(args.get(arg)), fmt), "invalid_arg_format", f"{tool_name}: {arg}={args.get(arg)!r} 不符合格式 {fmt}", checks, failures, notes)

    if rules.get("requires_authorization_context"):
        ok = _case_has_authorization_context(case)
        _record(ok, "missing_authorization_context", f"{tool_name}: 高风险工具需要 case 中存在授权/审批/用途/范围上下文", checks, failures, notes)


def _registry_by_name(tool_registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in tool_registry.get("tools") or [] if tool.get("name")}


def _record(ok: bool, failure_type: str, message: str, checks: list[bool], failures: list[str], notes: list[str]) -> None:
    checks.append(ok)
    if not ok:
        failures.append(failure_type)
        notes.append(f"[TOOL SCHEMA] {message}")


def _has_arg(args: dict[str, Any], key: str) -> bool:
    value = args.get(key)
    return value is not None and value != "" and value != []


def _matches_format(value: str, fmt: str) -> bool:
    if fmt == "yyyyMMdd":
        return bool(re.fullmatch(r"\d{8}", value))
    if fmt == "HH:mm:ss":
        return bool(re.fullmatch(r"\d{2}:\d{2}:\d{2}", value))
    return True


def _case_has_authorization_context(case: dict[str, Any]) -> bool:
    expected = case.get("expected") or {}
    understanding = expected.get("understanding") or {}
    if understanding.get("authorization_context_present"):
        return True
    text = " ".join(str(value) for value in [case.get("user_task"), case.get("task_type"), case.get("level")])
    return any(token in text for token in ["授权", "审批", "合规", "authorized", "authorization"])
