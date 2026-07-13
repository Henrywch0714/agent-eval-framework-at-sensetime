from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .provenance import attach_provenance
from .schema import JsonDict, SCHEMA_RUN, SCHEMA_TRACE


def load_jsonl(path: Path) -> list[JsonDict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_jsonl(path: Path, rows: list[JsonDict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def normalize_capture(
    events_path: Path,
    image_detail_limit: int = 10,
    tool_aliases: dict[str, str] | None = None,
    skill_map: dict[str, str] | None = None,
    tool_registry: dict[str, Any] | None = None,
    normalizer_config: dict[str, Any] | None = None,
) -> tuple[list[JsonDict], list[JsonDict]]:
    events = load_jsonl(events_path)
    config = normalizer_config or {}
    raw_runs = _merge_interaction_continuations(_build_runs(events, config), tool_aliases=tool_aliases)
    runs = []
    trace = []
    for index, raw_run in enumerate(raw_runs, 1):
        run, run_trace = normalize_run(
            index,
            raw_run,
            image_detail_limit=image_detail_limit,
            tool_aliases=tool_aliases,
            skill_map=skill_map,
            tool_registry=tool_registry,
            normalizer_config=normalizer_config,
        )
        runs.append(run)
        trace.extend(run_trace)
    return runs, trace


def _build_runs(events: list[JsonDict], config: dict[str, Any]) -> list[JsonDict]:
    runs: list[JsonDict] = []
    current: JsonDict | None = None
    last_run: JsonDict | None = None
    for event in events:
        kind = event.get("kind")
        if _is_run_start_event(event, config):
            current = {
                "request": _parse_json(event.get("post_data_preview") or "{}"),
                "request_event": event,
                "messages": [],
                "image_summaries": [],
                "events": [event],
            }
            runs.append(current)
            last_run = current
            continue
        if _is_retry_start_event(event, config) and last_run is not None:
            current = last_run
            current.setdefault("events", []).append(event)
            current.setdefault("messages", []).append({"__attempt_boundary__": True, "reason": "retry", "ts": event.get("ts")})
            current.setdefault("continuations", []).append(
                {
                    "type": "retry",
                    "url": event.get("url"),
                    "ts": event.get("ts"),
                }
            )
            continue
        if current is not None:
            current["events"].append(event)
            if kind == "sse_message":
                current["messages"].append(event.get("data"))
            elif kind == "multimodal_summary":
                current["image_summaries"].append(event.get("summary") or {})
            elif kind == "sse_close":
                current = None
            continue
        if last_run is not None and kind == "multimodal_summary":
            last_run["image_summaries"].append(event.get("summary") or {})
    return runs


def _is_run_start_event(event: JsonDict, config: dict[str, Any]) -> bool:
    if event.get("kind") != "network_request":
        return False
    parts = ((config.get("event_grouping") or {}).get("run_request_url_parts") or [])
    if parts:
        return any(part in str(event.get("url") or "") for part in parts)
    payload = _parse_json(event.get("post_data_preview") or "{}")
    return isinstance(payload.get("newMessage"), dict)


def _is_retry_start_event(event: JsonDict, config: dict[str, Any]) -> bool:
    if event.get("kind") != "network_request":
        return False
    parts = ((config.get("event_grouping") or {}).get("retry_request_url_parts") or [])
    return bool(parts) and any(part in str(event.get("url") or "") for part in parts)


def _merge_interaction_continuations(runs: list[JsonDict], tool_aliases: dict[str, str] | None = None) -> list[JsonDict]:
    """Merge tool-driven user input continuations into the parent user turn.

    Some agent runtimes emit a fresh request after a tool asks for
    `request_user_input`. For evaluation, that continuation is still part of
    the same task trajectory: image parsing -> user selects target -> capture
    search -> answer. Keeping it split would under-score tool order and final
    response quality.
    """
    merged: list[JsonDict] = []
    for run in runs:
        request_text = _request_text(run.get("request") or {})
        if request_text.startswith("functionResponse:request_user_input") and merged and _has_tool_call(merged[-1], "request_user_input", tool_aliases):
            parent = merged[-1]
            parent.setdefault("messages", []).extend(run.get("messages") or [])
            parent.setdefault("image_summaries", []).extend(run.get("image_summaries") or [])
            parent.setdefault("events", []).extend(run.get("events") or [])
            parent.setdefault("continuations", []).append(request_text)
            continue
        merged.append(run)
    return merged


def _has_tool_call(raw_run: JsonDict, tool_name: str, tool_aliases: dict[str, str] | None = None) -> bool:
    return any(item.get("kind") == "call" and item.get("name") == tool_name for item in _extract_tool_items(raw_run.get("messages") or [], tool_aliases))


def normalize_run(
    index: int,
    raw_run: JsonDict,
    image_detail_limit: int = 10,
    tool_aliases: dict[str, str] | None = None,
    skill_map: dict[str, str] | None = None,
    tool_registry: dict[str, Any] | None = None,
    normalizer_config: dict[str, Any] | None = None,
) -> tuple[JsonDict, list[JsonDict]]:
    request = raw_run.get("request") or {}
    messages = raw_run.get("messages") or []
    tool_items = _extract_tool_items(messages, tool_aliases)
    response_texts = _extract_response_texts(messages)
    usage = _extract_usage(messages)
    task = _request_text(request)
    config = normalizer_config or {}
    image_summaries = raw_run.get("image_summaries") or _image_summaries_from_tool_results(tool_items, image_detail_limit, config)
    final_text = response_texts[-1] if response_texts else ""
    run_id = f"RUN-{index:04d}"
    tool_chain = _normalize_tool_chain(tool_items, config)
    tool_results = _summarize_tool_results(tool_items, run_id, tool_registry or {}, config)
    data_lineage = attach_provenance(tool_chain, tool_results, tool_registry=tool_registry)

    observed = {
        "token_usage": _summarize_usage(usage),
        "task_understanding": _infer_task_understanding(task, tool_items, config),
        "plan": _extract_plan(tool_items),
        "skill_chain": _infer_skill_chain(tool_items, skill_map=skill_map),
        "tool_chain": tool_chain,
        "tool_results": tool_results,
        "data_lineage": data_lineage,
        "tool_args": _summarize_tool_args(tool_items, config),
        "oracle_evidence": _summarize_oracle_evidence(image_summaries),
        "final_response": {
            "text": final_text,
            "claims": _extract_response_claims(final_text, config),
        },
        "safety_flags": _detect_safety_flags(task, final_text, config),
    }

    run = {
        "schema_version": SCHEMA_RUN,
        "run_id": run_id,
        "source_index": index,
        "session_id": request.get("sessionId"),
        "user_task": task,
        "observed": observed,
    }
    return run, _trace_from_run(run, tool_items, image_summaries, config)


def _parse_json(text: str) -> JsonDict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"raw": value}
    except Exception:
        return {"raw": text[:1000]}


def _tool_defs(tool_registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in tool_registry.get("tools") or [] if tool.get("name")}


def _get_path(value: Any, path: list[Any]) -> Any:
    current = value
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return None
    return current


def _first_path(value: Any, paths: list[list[Any]]) -> Any:
    for path in paths:
        found = _get_path(value, path)
        if found not in (None, "", [], {}):
            return found
    return None


def _first_key(value: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if value.get(key) not in (None, "", [], {}):
            return value.get(key)
    return None


def _request_text(request: JsonDict) -> str:
    parts = ((request.get("newMessage") or {}).get("parts") or [])
    if not parts:
        return ""
    first = parts[0]
    if isinstance(first, dict) and "text" in first:
        return str(first.get("text") or "")
    if isinstance(first, dict) and "functionResponse" in first:
        response = first["functionResponse"]
        return f"functionResponse:{response.get('name')} => {response.get('response')}"
    return json.dumps(first, ensure_ascii=False)


def _extract_tool_items(messages: list[Any], tool_aliases: dict[str, str] | None = None) -> list[JsonDict]:
    items = []
    attempt = 1
    for data in messages:
        if not isinstance(data, dict):
            continue
        if data.get("__attempt_boundary__"):
            attempt += 1
            items.append({"kind": "attempt_boundary", "attempt": attempt, "reason": data.get("reason")})
            continue
        for part in (data.get("content") or {}).get("parts") or []:
            if not isinstance(part, dict):
                continue
            if "functionCall" in part:
                call = part["functionCall"]
                raw_name = call.get("name")
                items.append(
                    {
                        "kind": "call",
                        "attempt": attempt,
                        "name": _canonical_tool_name(raw_name, tool_aliases),
                        "raw_name": raw_name,
                        "id": call.get("id"),
                        "args": call.get("args") or {},
                    }
                )
            if "functionResponse" in part:
                response = part["functionResponse"]
                raw_name = response.get("name")
                items.append(
                    {
                        "kind": "result",
                        "attempt": attempt,
                        "name": _canonical_tool_name(raw_name, tool_aliases),
                        "raw_name": raw_name,
                        "id": response.get("id"),
                        "response": response.get("response"),
                    }
                )
    return items


def _canonical_tool_name(name: Any, tool_aliases: dict[str, str] | None = None) -> str:
    text = str(name or "")
    return (tool_aliases or {}).get(text, text)


def _extract_response_texts(messages: list[Any]) -> list[str]:
    texts = []
    for data in messages:
        if not isinstance(data, dict) or data.get("partial") is not False:
            continue
        parts = (data.get("content") or {}).get("parts") or []
        if any(isinstance(part, dict) and "functionCall" in part for part in parts):
            continue
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        if text.strip():
            texts.append(text.strip())
    return texts


def _extract_usage(messages: list[Any]) -> list[JsonDict]:
    return [msg["usageMetadata"] for msg in messages if isinstance(msg, dict) and isinstance(msg.get("usageMetadata"), dict)]


def _infer_task_understanding(task: str, tool_items: list[JsonDict], config: dict[str, Any]) -> JsonDict:
    rules = config.get("task_understanding") or {}
    args = _merged_call_args(tool_items)
    feature_arg = rules.get("feature_arg")
    target_type_arg = rules.get("target_type_arg")
    appearance = str(args.get(feature_arg) or "") if feature_arg else ""
    search_type = str(args.get(target_type_arg) or "") if target_type_arg else ""
    has_image = any(_has_arg(args, key) for key in rules.get("image_args") or []) or any(marker in task for marker in rules.get("image_markers") or [])
    has_point = any(item.get("name") in set(rules.get("point_tools") or []) for item in tool_items) or any(marker in task for marker in rules.get("point_markers") or [])
    intent_labels = rules.get("intent_labels") or {}
    if has_image and has_point:
        intent = intent_labels.get("image_and_point", "image_and_point")
    elif has_image:
        intent = intent_labels.get("image", "image")
    elif has_point and appearance:
        intent = intent_labels.get("point_feature", "point_feature")
    elif appearance or any(marker in task for marker in rules.get("text_search_markers") or []):
        intent = intent_labels.get("text_feature", "text_feature")
    else:
        intent = intent_labels.get("unknown", "unknown")
    return {
        "intent": intent,
        "target_type": _target_type(search_type, f"{task} {appearance}", rules),
        "features": _features(f"{task} {appearance}", rules),
        "time_range_days": _time_range_days(task, args, rules),
        "has_image_input": has_image,
        "has_point_constraint": has_point,
    }


def _merged_call_args(tool_items: list[JsonDict]) -> JsonDict:
    merged: JsonDict = {}
    for item in tool_items:
        if item.get("kind") == "call" and isinstance(item.get("args"), dict):
            merged.update(item["args"])
    return merged


def _has_arg(args: dict[str, Any], key: str) -> bool:
    value = args.get(key)
    return value is not None and value != "" and value != []


def _target_type(search_type: str, text: str, rules: dict[str, Any]) -> str:
    value = search_type.upper()
    mapped = (rules.get("target_type_map") or {}).get(value)
    if mapped:
        return mapped
    for item in rules.get("target_type_text_keywords") or []:
        if any(token in text for token in item.get("keywords") or []):
            return item.get("target_type") or "UNKNOWN"
    return "UNKNOWN"


def _features(text: str, rules: dict[str, Any]) -> JsonDict:
    features: JsonDict = {}
    for field, value_map in (rules.get("feature_keywords") or {}).items():
        for value, keywords in value_map.items():
            if any(token in text for token in keywords):
                features[field] = True if str(value).lower() == "true" else value
                break
    return features


def _time_range_days(task: str, args: JsonDict, rules: dict[str, Any] | None = None) -> int | None:
    rules = rules or {}
    for item in rules.get("time_range_markers") or []:
        if any(token in task for token in item.get("keywords") or []):
            return item.get("days")
    date_arg_names = rules.get("date_arg_names") or ["start_date", "end_date"]
    start = str(args.get(date_arg_names[0]) or "") if len(date_arg_names) > 0 else ""
    end = str(args.get(date_arg_names[1]) or "") if len(date_arg_names) > 1 else ""
    if re.fullmatch(r"\d{8}", start) and re.fullmatch(r"\d{8}", end):
        try:
            from datetime import datetime as dt

            return max(1, (dt.strptime(end, "%Y%m%d") - dt.strptime(start, "%Y%m%d")).days)
        except ValueError:
            return None
    return None


def _extract_plan(tool_items: list[JsonDict]) -> list[str]:
    for item in tool_items:
        if item.get("kind") == "call" and item.get("name") == "update_plan":
            args = item.get("args") or {}
            if isinstance(args.get("steps"), list):
                return [str(step) for step in args["steps"]]
            if args.get("plan"):
                return [str(args["plan"])]
    return [str(item.get("name")) for item in tool_items if item.get("kind") == "call" and item.get("name")]


def _infer_skill_chain(tool_items: list[JsonDict], skill_map: dict[str, str] | None = None) -> list[JsonDict]:
    chain = []
    seen = set()
    for item in tool_items:
        if item.get("kind") != "call":
            continue
        if item.get("name") == "load_skill":
            skill = str((item.get("args") or {}).get("name") or (skill_map or {}).get("load_skill") or "unknown")
        else:
            skill = (skill_map or {}).get(str(item.get("name"))) or "unknown"
        key = (skill, item.get("name"))
        if key in seen:
            continue
        seen.add(key)
        chain.append({"skill_name": skill, "via_tool": item.get("name")})
    return chain


def _normalize_tool_chain(tool_items: list[JsonDict], config: dict[str, Any]) -> list[JsonDict]:
    order = 0
    chain = []
    for item in tool_items:
        if item.get("kind") != "call":
            continue
        order += 1
        chain.append(
            {
                "order": order,
                "attempt": item.get("attempt") or 1,
                "tool_name": item.get("name"),
                "args": _compact_args(item.get("args") or {}, config),
            }
        )
    return chain


def _summarize_tool_results(tool_items: list[JsonDict], run_id: str, tool_registry: dict[str, Any], config: dict[str, Any]) -> list[JsonDict]:
    order = 0
    results = []
    tool_defs = _tool_defs(tool_registry)
    for item in tool_items:
        if item.get("kind") == "call":
            order += 1
            continue
        if item.get("kind") != "result":
            continue
        result_key = f"{run_id}.tool_{order:03d}.result"
        summary = {
            "after_order": order,
            "attempt": item.get("attempt") or 1,
            "tool_name": item.get("name"),
            "result_key": result_key,
            **_data_flow_summary(item.get("name"), item.get("response"), tool_defs, config),
        }
        _attach_child_result_keys(summary)
        results.append(summary)
    return results


def _attach_child_result_keys(summary: JsonDict) -> None:
    base = summary.get("result_key")
    if not base:
        return
    for key, value in list(summary.items()):
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            continue
        for idx, child in enumerate(value):
            child["result_key"] = f"{base}.{key}[{idx}]"


def _data_flow_summary(tool_name: str, response: Any, tool_defs: dict[str, dict[str, Any]], config: dict[str, Any]) -> JsonDict:
    if not isinstance(response, dict):
        return {}
    spec = _summary_spec_for_tool(tool_name, tool_defs, config)
    if not spec:
        return {}
    kind = spec.get("kind")
    if kind == "detected_targets":
        return _summary_detected_targets(response, spec)
    if kind == "list_refs":
        return _summary_list_refs(response, spec)
    if kind == "query_param":
        return _summary_query_param(response, spec)
    if kind == "recursive_presence":
        return _summary_recursive_presence(response, spec)
    return {}


def _summary_spec_for_tool(tool_name: str, tool_defs: dict[str, dict[str, Any]], config: dict[str, Any]) -> JsonDict | None:
    category = (tool_defs.get(tool_name) or {}).get("category")
    for spec in config.get("tool_result_summaries") or []:
        if spec.get("tool") == tool_name:
            return spec
        if category and category in (spec.get("tool_categories") or []):
            return spec
    return None


def _summary_detected_targets(response: JsonDict, spec: JsonDict) -> JsonDict:
    result = _get_path(response, spec.get("result_path") or [])
    targets = []
    if not isinstance(result, dict):
        result = {}
    for key, target_type in (spec.get("target_collections") or {}).items():
        for idx, item in enumerate(result.get(key) or [], 1):
            position = item.get(spec.get("position_key") or "position") if isinstance(item, dict) else {}
            value = _position_to_box(position or {}, spec)
            if value:
                targets.append({spec.get("target_type_key") or "target_type": target_type, "index": idx, spec.get("value_key") or "value": value})
    out: JsonDict = {spec.get("output_collection") or "items": targets}
    out.update(_presence_outputs(response, spec.get("presence_outputs") or {}))
    return out


def _summary_list_refs(response: JsonDict, spec: JsonDict) -> JsonDict:
    items = _get_path(response, spec.get("list_path") or [])
    if not isinstance(items, list):
        items = []
    limit = int(spec.get("limit") or 20)
    values = [str(item.get(spec.get("item_value_key"))) for item in items if isinstance(item, dict) and item.get(spec.get("item_value_key"))]
    refs_key = spec.get("output_refs_key") or "refs"
    ref_value_key = spec.get("ref_value_key") or "value"
    out = {
        spec.get("output_list_key") or "values": values[:limit],
        refs_key: [{ref_value_key: value, "index": idx} for idx, value in enumerate(values[:limit])],
    }
    count_value = _get_path(response, spec.get("count_path") or []) or len(values)
    for key in spec.get("count_output_keys") or []:
        out[key] = count_value
    return out


def _summary_query_param(response: JsonDict, spec: JsonDict) -> JsonDict:
    values = _get_path(response, spec.get("list_path") or [])
    if not isinstance(values, list):
        values = []
    limit = int(spec.get("limit") or 20)
    out: JsonDict = {
        "query_type": _get_path(response, spec.get("query_type_path") or []),
        spec.get("output_list_key") or "values": [str(item) for item in values[:limit]],
        spec.get("count_output_key") or "value_count": len(values),
        spec.get("image_ref_output_key") or "image_ref_present": bool(_get_path(response, spec.get("image_ref_path") or [])),
    }
    position = _get_path(response, spec.get("position_path") or [])
    out[spec.get("position_output_key") or "position"] = _position_to_box(position or {}, spec)
    return out


def _summary_recursive_presence(response: JsonDict, spec: JsonDict) -> JsonDict:
    out: JsonDict = {}
    for output_key, keys in (spec.get("presence_outputs") or {}).items():
        if isinstance(keys, list):
            out[output_key] = any(_contains_key(response, key) for key in keys)
        else:
            out[output_key] = _contains_key(response, str(keys))
    return out


def _position_to_box(position: JsonDict, spec: JsonDict | None = None) -> JsonDict | None:
    spec = spec or {}
    keys = spec.get("box_output_keys") or ["left", "top", "right", "bottom"]
    start = position.get("start") if isinstance(position.get("start"), dict) else {}
    end = position.get("end") if isinstance(position.get("end"), dict) else {}
    if all(key in start for key in ["x", "y"]) and all(key in end for key in ["x", "y"]):
        return {
            keys[0]: start["x"],
            keys[1]: start["y"],
            keys[2]: end["x"],
            keys[3]: end["y"],
        }
    return None


def _presence_outputs(response: JsonDict, outputs: dict[str, Any]) -> JsonDict:
    return {key: bool(_get_path(response, path if isinstance(path, list) else [path])) for key, path in outputs.items()}


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _contains_position(value: Any) -> bool:
    if isinstance(value, dict):
        if _position_to_box(value.get("position") or {}):
            return True
        return any(_contains_position(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_position(item) for item in value)
    return False


def _compact_args(args: JsonDict, config: dict[str, Any]) -> JsonDict:
    rules = config.get("argument_compaction") or {}
    keep = set(rules.get("keep_args") or args.keys())
    redact = set(rules.get("redact_args") or [])
    return {key: ("<redacted_ref>" if key in redact else value) for key, value in args.items() if key in keep}


def _summarize_tool_args(tool_items: list[JsonDict], config: dict[str, Any]) -> JsonDict:
    rules = config.get("tool_arg_summary") or {}
    args = _merged_call_args(tool_items)
    out = {out_key: args.get(arg_key) for out_key, arg_key in (rules.get("fields") or {}).items()}
    task_rules = config.get("task_understanding") or {}
    out["time_range_days"] = _time_range_days("", args, {**task_rules, "date_arg_names": rules.get("date_args") or task_rules.get("date_arg_names") or []})
    for out_key, required_args in (rules.get("presence") or {}).items():
        out[out_key] = all(_has_arg(args, key) for key in required_args)
    return out


def _image_summaries_from_tool_results(tool_items: list[JsonDict], image_detail_limit: int, config: dict[str, Any]) -> list[JsonDict]:
    rules = config.get("oracle_evidence") or {}
    summaries = []
    for item in tool_items:
        if item.get("kind") != "result":
            continue
        response = item.get("response")
        data = response.get("data") if isinstance(response, dict) and isinstance(response.get("data"), dict) else {}
        query_type = _get_path(response, rules.get("query_type_path") or [])
        if rules.get("result_set_query_types") and query_type not in set(rules.get("result_set_query_types") or []):
            continue
        results = _get_path(response, rules.get("result_list_path") or [])
        if not isinstance(results, list):
            continue
        summaries.append(_summarize_result_set(response, results[:image_detail_limit], rules))
    return summaries


def _summarize_result_set(response: JsonDict, results: list[JsonDict], rules: dict[str, Any]) -> JsonDict:
    score_key = rules.get("score_key") or "score"
    scores = [float(item[score_key]) for item in results if isinstance(item, dict) and isinstance(item.get(score_key), (int, float))]
    points = Counter()
    top_k = []
    for idx, item in enumerate(results, 1):
        point = _first_path(item, rules.get("point_ref_paths") or []) or "unknown"
        points[str(point)] += 1
        top_k.append(
            {
                "rank": idx,
                "evidence_id": _first_key(item, rules.get("evidence_id_keys") or []) or f"ev-{idx:03d}",
                "score": item.get(score_key),
                "capture_type": item.get(rules.get("capture_type_key") or "capture_type"),
                "point_ref": point,
            }
        )
    return {
        "query_type": _get_path(response, rules.get("query_type_path") or []),
        "page": _get_path(response, rules.get("page_path") or []) or {},
        "result_count_observed": len(results),
        "score_stats": _score_stats(scores),
        "distinct_points": len(points) if points else None,
        "point_summary": [{"point_ref": key, "count": val} for key, val in points.most_common(5)],
        "top_k_refs": top_k,
    }


def _summarize_oracle_evidence(image_summaries: list[JsonDict]) -> JsonDict:
    if not image_summaries:
        return {"image_result_sets": 0, "total_count": None, "top_k_count": 0, "score_stats": {}, "distinct_points": None}
    chosen = image_summaries[-1]
    page = chosen.get("page") or {}
    top_k_refs = chosen.get("top_k_refs") or _top_k_refs_from_results(chosen.get("results") or [])
    point_summary = chosen.get("point_summary") or _point_summary_from_top_k(top_k_refs)
    distinct_points = chosen.get("distinct_points")
    if distinct_points is None and point_summary:
        distinct_points = len(point_summary)
    return {
        "image_result_sets": len(image_summaries),
        "query_type": chosen.get("query_type"),
        "total_count": page.get("total"),
        "top_k_count": chosen.get("result_count_observed") or len(top_k_refs),
        "score_stats": chosen.get("score_stats") or chosen.get("score_stats_observed") or {},
        "distinct_points": distinct_points,
        "point_summary": point_summary,
        "top_k_refs": top_k_refs,
    }


def _top_k_refs_from_results(results: list[JsonDict]) -> list[JsonDict]:
    out = []
    for idx, item in enumerate(results, 1):
        out.append(
            {
                "rank": idx,
                "evidence_id": item.get("id") or item.get("result_id") or f"ev-{idx:03d}",
                "score": item.get("score"),
                "capture_type": item.get("type"),
                "point_ref": item.get("point_ref") or "unknown",
            }
        )
    return out


def _point_summary_from_top_k(top_k_refs: list[JsonDict]) -> list[JsonDict]:
    counts = Counter(str(item.get("point_ref") or "unknown") for item in top_k_refs)
    return [{"point_ref": key, "count": value} for key, value in counts.most_common(5)]


def _score_stats(scores: list[float]) -> JsonDict:
    if not scores:
        return {}
    return {"count": len(scores), "min": min(scores), "avg": statistics.mean(scores), "max": max(scores)}


def _summarize_usage(usage: list[JsonDict]) -> JsonDict:
    if not usage:
        return {"steps": 0, "prompt_tokens": 0, "candidate_tokens": 0, "total_tokens": 0}
    latest = usage[-1]
    return {
        "steps": len(usage),
        "prompt_tokens": int(latest.get("promptTokenCount") or 0),
        "candidate_tokens": int(latest.get("candidatesTokenCount") or 0),
        "total_tokens": int(latest.get("totalTokenCount") or 0),
    }


def _extract_response_claims(text: str, config: dict[str, Any]) -> JsonDict:
    rules = config.get("response_claims") or {}
    claims: JsonDict = {
        "contains_identity_conclusion": _matches_any_pattern(text, rules.get("identity_conclusion_patterns") or []),
        "contains_law_enforcement_conclusion": _matches_any_pattern(text, rules.get("law_enforcement_conclusion_patterns") or []),
        "contains_permission_bypass": any(token in text for token in rules.get("permission_bypass_tokens") or []),
    }
    total = _first_int_after(text, rules.get("total_prefixes") or [])
    if total is not None:
        claims["total_count"] = total
    points = _first_int_before(text, rules.get("distinct_point_suffixes") or [])
    if points is not None:
        claims["distinct_points"] = points
    high = _first_int_near(text, rules.get("high_confidence_anchors") or [])
    if high is not None:
        claims["high_confidence_count"] = high
    return claims


def _matches_any_pattern(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _first_int_after(text: str, prefixes: list[str]) -> int | None:
    for prefix in prefixes:
        match = re.search(re.escape(prefix) + r"\D{0,8}(\d+)", text)
        if match:
            return int(match.group(1))
    return None


def _first_int_before(text: str, suffixes: list[str]) -> int | None:
    for suffix in suffixes:
        match = re.search(r"(\d+)\s*" + re.escape(suffix), text)
        if match:
            return int(match.group(1))
    return None


def _first_int_near(text: str, anchors: list[str]) -> int | None:
    for anchor in anchors:
        idx = text.find(anchor)
        if idx == -1:
            continue
        before = text[max(0, idx - 30) : idx + len(anchor)]
        before_match = re.search(r"(\d+)\s*条.{0,16}" + re.escape(anchor), before)
        if before_match:
            return int(before_match.group(1))
        window = text[idx : idx + 80]
        match = re.search(r"(?:有|共|约)?\s*(\d+)\s*条", window)
        if match:
            return int(match.group(1))
    return None


def _detect_safety_flags(task: str, response: str, config: dict[str, Any]) -> list[str]:
    rules = config.get("safety_flags") or {}
    text = f"{task}\n{response}"
    flags = []
    for flag, tokens in (rules.get("task_tokens") or {}).items():
        if any(token in task for token in tokens):
            flags.append(flag)
    for flag, tokens in (rules.get("response_tokens") or {}).items():
        if any(token in response for token in tokens):
            flags.append(flag)
    for rule in rules.get("combined_rules") or []:
        if any(token in text for token in rule.get("required_any") or []) and any(token in text for token in rule.get("with_any") or []):
            flags.append(rule.get("flag"))
    for rule in rules.get("regex_flags") or []:
        target_text = response if rule.get("target") == "response" else text
        if re.search(rule.get("pattern") or r"$^", target_text):
            flags.append(rule.get("flag"))
    return sorted(set(flags))


def _trace_from_run(run: JsonDict, tool_items: list[JsonDict], image_summaries: list[JsonDict], config: dict[str, Any]) -> list[JsonDict]:
    seq = 1
    base = {"schema_version": SCHEMA_TRACE, "run_id": run["run_id"], "case_id": None}
    events = [
        _trace_event(base, seq, "user_task", "user", {"text": run["user_task"]}),
        _trace_event(base, seq + 1, "task_understanding", "agent", run["observed"]["task_understanding"]),
    ]
    seq += 2
    events.append(_trace_event(base, seq, "agent_plan", "agent", {"steps": run["observed"]["plan"]}))
    seq += 1
    for item in tool_items:
        if item.get("kind") == "call":
            payload = {"tool_name": item.get("name"), "args": _compact_args(item.get("args") or {}, config)}
            events.append(_trace_event(base, seq, "tool_call", "agent", payload))
        else:
            payload = {"tool_name": item.get("name"), "oracle_assumption": True, "summary": _response_summary(item.get("response"))}
            events.append(_trace_event(base, seq, "tool_result_summary", "tool", payload))
        seq += 1
    for summary in image_summaries:
        events.append(_trace_event(base, seq, "oracle_evidence_summary", "tool", summary))
        seq += 1
    events.append(_trace_event(base, seq, "final_response", "agent", run["observed"]["final_response"]))
    return events


def _trace_event(base: JsonDict, seq: int, event_type: str, actor: str, payload: JsonDict) -> JsonDict:
    return {**base, "seq": seq, "timestamp": datetime.now().isoformat(timespec="seconds"), "event_type": event_type, "actor": actor, "payload": payload}


def _response_summary(response: Any) -> JsonDict:
    if not isinstance(response, dict):
        return {"raw": str(response)[:300]}
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    if data:
        return {"status": response.get("status"), "page": data.get("page"), "result_count": len(data.get("result") or [])}
    return {"summary": response.get("summary")}
