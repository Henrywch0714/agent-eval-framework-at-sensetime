from __future__ import annotations

import json
from typing import Any

from .schema import JsonDict


DEFAULT_LOG_ADAPTER = {
    "event_kind_field": "kind",
    "request_event_kind": "network_request",
    "request_body_field": "post_data_preview",
    "run_request_url_parts_path": ["event_grouping", "run_request_url_parts"],
    "retry_request_url_parts_path": ["event_grouping", "retry_request_url_parts"],
    "message_event_kinds": ["sse_message"],
    "message_data_path": ["data"],
    "evidence_summary_event_kinds": ["multimodal_summary"],
    "evidence_summary_path": ["summary"],
    "json_response_event_kinds": ["xhr_json_response", "fetch_json_response"],
    "close_event_kinds": ["sse_close"],
    "request_text": {
        "parts_path": ["newMessage", "parts"],
        "text_key": "text",
        "function_response_key": "functionResponse",
        "function_response_name_key": "name",
        "function_response_payload_key": "response",
    },
    "tool_trace": {
        "parts_path": ["content", "parts"],
        "call_key": "functionCall",
        "result_key": "functionResponse",
        "name_key": "name",
        "id_key": "id",
        "args_key": "args",
        "response_key": "response",
    },
    "final_response": {
        "partial_key": "partial",
        "final_value": False,
        "text_key": "text",
        "skip_if_tool_call_present": True,
        "streaming_fallback": False,
        "stream_value": True,
        "strip_blocks": [],
    },
    "usage": {"path": ["usageMetadata"]},
    "runtime_errors": {
        "message_error_key": "error",
        "tool_error_statuses": ["error", "failed", "failure"],
        "tool_error_keys": ["error", "error_message"],
    },
}


def build_runs(events: list[JsonDict], config: dict[str, Any]) -> list[JsonDict]:
    adapter = _adapter(config)
    runs: list[JsonDict] = []
    current: JsonDict | None = None
    last_run: JsonDict | None = None
    for event in events:
        kind = _event_kind(event, adapter)
        if is_run_start_event(event, config):
            current = {
                "request": _parse_json(str(event.get(adapter.get("request_body_field") or "post_data_preview") or "{}")),
                "request_event": event,
                "messages": [],
                "evidence_summaries": [],
                "events": [event],
            }
            runs.append(current)
            last_run = current
            continue
        if is_retry_start_event(event, config) and last_run is not None:
            current = last_run
            current.setdefault("events", []).append(event)
            current.setdefault("messages", []).append({"__attempt_boundary__": True, "reason": "retry", "ts": event.get("ts")})
            current.setdefault("continuations", []).append({"type": "retry", "url": event.get("url"), "ts": event.get("ts")})
            continue
        if current is not None:
            current["events"].append(event)
            if kind in set(adapter.get("message_event_kinds") or []):
                current["messages"].append(_get_path(event, adapter.get("message_data_path") or ["data"]))
            elif kind in set(adapter.get("evidence_summary_event_kinds") or []):
                current["evidence_summaries"].append(_get_path(event, adapter.get("evidence_summary_path") or ["summary"]) or {})
            elif kind in set(adapter.get("json_response_event_kinds") or []):
                current["evidence_summaries"].extend(summaries_from_json_response_event(event))
            elif kind in set(adapter.get("close_event_kinds") or []):
                current = None
            continue
        if last_run is not None and kind in set(adapter.get("evidence_summary_event_kinds") or []):
            last_run["evidence_summaries"].append(_get_path(event, adapter.get("evidence_summary_path") or ["summary"]) or {})
        elif last_run is not None and kind in set(adapter.get("json_response_event_kinds") or []):
            last_run["evidence_summaries"].extend(summaries_from_json_response_event(event))
    return runs


def summaries_from_json_response_event(event: JsonDict) -> list[JsonDict]:
    diagnostic = event.get("diagnostic") or {}
    summaries = []
    for item in diagnostic.get("result_sets") or []:
        if not isinstance(item, dict):
            continue
        summaries.append(
            {
                "source": event.get("kind"),
                "url": event.get("url"),
                "query_type": item.get("query_type"),
                "page": item.get("page") or {},
                "result_count_observed": item.get("result_len") or len(item.get("grounding_items") or []),
                "grounding_items": item.get("grounding_items") or [],
                "items": item.get("items") or item.get("grounding_items") or [],
                "evidence_type": item.get("evidence_type"),
                "group_summary_field": item.get("group_summary_field") or item.get("point_summary_field"),
                "point_summary_field": item.get("point_summary_field"),
                "time_stats_field": item.get("time_stats_field"),
            }
        )
    return summaries


def is_run_start_event(event: JsonDict, config: dict[str, Any]) -> bool:
    adapter = _adapter(config)
    if _event_kind(event, adapter) != adapter.get("request_event_kind", "network_request"):
        return False
    parts = _configured_url_parts(config, adapter.get("run_request_url_parts_path") or ["event_grouping", "run_request_url_parts"])
    if parts:
        return any(part in str(event.get("url") or "") for part in parts)
    payload = _parse_json(str(event.get(adapter.get("request_body_field") or "post_data_preview") or "{}"))
    required_path = adapter.get("run_payload_required_path") or ["newMessage"]
    return _get_path(payload, required_path) not in (None, "", [], {})


def is_retry_start_event(event: JsonDict, config: dict[str, Any]) -> bool:
    adapter = _adapter(config)
    if _event_kind(event, adapter) != adapter.get("request_event_kind", "network_request"):
        return False
    parts = _configured_url_parts(config, adapter.get("retry_request_url_parts_path") or ["event_grouping", "retry_request_url_parts"])
    return bool(parts) and any(part in str(event.get("url") or "") for part in parts)


def request_text(request: JsonDict, config: dict[str, Any]) -> str:
    rules = _adapter(config).get("request_text") or {}
    parts = _get_path(request, rules.get("parts_path") or ["newMessage", "parts"])
    if not isinstance(parts, list) or not parts:
        return ""
    first = parts[0]
    if isinstance(first, dict) and rules.get("text_key", "text") in first:
        return str(first.get(rules.get("text_key", "text")) or "")
    function_key = rules.get("function_response_key") or "functionResponse"
    if isinstance(first, dict) and function_key in first:
        response = first[function_key] or {}
        name_key = rules.get("function_response_name_key") or "name"
        payload_key = rules.get("function_response_payload_key") or "response"
    return f"functionResponse:{response.get(name_key)} => {response.get(payload_key)}"
    return json.dumps(first, ensure_ascii=False)


def is_tool_response_continuation(text: str, tool_name: str, config: dict[str, Any]) -> bool:
    rules = _adapter(config).get("request_text") or {}
    prefix = rules.get("function_response_prefix") or "functionResponse:{tool_name}"
    return text.startswith(str(prefix).format(tool_name=tool_name))


def extract_tool_items(messages: list[Any], tool_aliases: dict[str, str] | None, config: dict[str, Any]) -> list[JsonDict]:
    rules = _adapter(config).get("tool_trace") or {}
    items = []
    attempt = 1
    for data in messages:
        if not isinstance(data, dict):
            continue
        if data.get("__attempt_boundary__"):
            attempt += 1
            items.append({"kind": "attempt_boundary", "attempt": attempt, "reason": data.get("reason")})
            continue
        parts = _get_path(data, rules.get("parts_path") or ["content", "parts"])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if rules.get("call_key", "functionCall") in part:
                call = part[rules.get("call_key", "functionCall")] or {}
                raw_name = call.get(rules.get("name_key") or "name")
                items.append(
                    {
                        "kind": "call",
                        "attempt": attempt,
                        "name": canonical_tool_name(raw_name, tool_aliases),
                        "raw_name": raw_name,
                        "id": call.get(rules.get("id_key") or "id"),
                        "args": call.get(rules.get("args_key") or "args") or {},
                    }
                )
            if rules.get("result_key", "functionResponse") in part:
                response = part[rules.get("result_key", "functionResponse")] or {}
                raw_name = response.get(rules.get("name_key") or "name")
                items.append(
                    {
                        "kind": "result",
                        "attempt": attempt,
                        "name": canonical_tool_name(raw_name, tool_aliases),
                        "raw_name": raw_name,
                        "id": response.get(rules.get("id_key") or "id"),
                        "response": response.get(rules.get("response_key") or "response"),
                    }
                )
    return items


def canonical_tool_name(name: Any, tool_aliases: dict[str, str] | None = None) -> str:
    text = str(name or "")
    return (tool_aliases or {}).get(text, text)


def extract_response_texts(messages: list[Any], config: dict[str, Any]) -> list[str]:
    response_rules = _adapter(config).get("final_response") or {}
    tool_rules = _adapter(config).get("tool_trace") or {}
    texts = []
    stream_chunks = []
    for data in messages:
        if not isinstance(data, dict):
            continue
        parts = _get_path(data, tool_rules.get("parts_path") or ["content", "parts"])
        if not isinstance(parts, list):
            continue
        if response_rules.get("skip_if_tool_call_present", True) and any(isinstance(part, dict) and tool_rules.get("call_key", "functionCall") in part for part in parts):
            continue
        text_key = response_rules.get("text_key") or "text"
        text = "".join(str(part.get(text_key) or "") for part in parts if isinstance(part, dict))
        if not text.strip():
            continue

        partial_key = response_rules.get("partial_key")
        if partial_key and data.get(partial_key) != response_rules.get("final_value", False):
            if response_rules.get("streaming_fallback") and data.get(partial_key) == response_rules.get("stream_value", True):
                stream_chunks.append(text)
            continue
        cleaned = _clean_response_text(text, response_rules)
        if cleaned.strip():
            texts.append(cleaned.strip())
    if not texts and stream_chunks:
        cleaned = _clean_response_text("".join(stream_chunks), response_rules)
        if cleaned.strip():
            texts.append(cleaned.strip())
    return texts


def _clean_response_text(text: str, response_rules: dict[str, Any]) -> str:
    cleaned = text
    for block in response_rules.get("strip_blocks") or []:
        name = str(block or "").strip()
        if not name:
            continue
        cleaned = re_sub_xml_block(name, cleaned)
    return cleaned.strip()


def re_sub_xml_block(name: str, text: str) -> str:
    import re

    escaped = re.escape(name)
    text = re.sub(rf"<{escaped}\b[^>]*>.*?</{escaped}>", "", text, flags=re.IGNORECASE | re.DOTALL)
    return re.sub(rf"<{escaped}\b[^>]*>.*$", "", text, flags=re.IGNORECASE | re.DOTALL)


def extract_usage(messages: list[Any], config: dict[str, Any]) -> list[JsonDict]:
    path = ((_adapter(config).get("usage") or {}).get("path") or ["usageMetadata"])
    return [value for msg in messages if isinstance((value := _get_path(msg, path)), dict)]


def extract_runtime_errors(messages: list[Any], config: dict[str, Any], clipper) -> list[JsonDict]:
    adapter = _adapter(config)
    error_rules = adapter.get("runtime_errors") or {}
    tool_rules = adapter.get("tool_trace") or {}
    errors = []
    for index, data in enumerate(messages, 1):
        if not isinstance(data, dict):
            continue
        message_error_key = error_rules.get("message_error_key") or "error"
        if data.get(message_error_key) not in (None, "", [], {}):
            errors.append({"message_index": index, "reason": "sse_error", "snippet": clipper(data.get(message_error_key))})
        parts = _get_path(data, tool_rules.get("parts_path") or ["content", "parts"])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or tool_rules.get("result_key", "functionResponse") not in part:
                continue
            response = (part[tool_rules.get("result_key", "functionResponse")] or {}).get(tool_rules.get("response_key") or "response")
            if not isinstance(response, dict):
                continue
            status = str(response.get("status") or "").lower()
            if status in set(error_rules.get("tool_error_statuses") or ["error", "failed", "failure"]):
                errors.append({"message_index": index, "reason": "tool_response_error_status", "snippet": clipper(response)})
            for key in error_rules.get("tool_error_keys") or ["error", "error_message"]:
                explicit_error = response.get(key)
                if explicit_error not in (None, "", [], {}):
                    errors.append({"message_index": index, "reason": "tool_response_error", "snippet": clipper(explicit_error)})
                    break
    return errors


def _adapter(config: dict[str, Any]) -> dict[str, Any]:
    return _deep_merge(DEFAULT_LOG_ADAPTER, config.get("log_adapter") or {})


def _event_kind(event: JsonDict, adapter: dict[str, Any]) -> str:
    return str(event.get(adapter.get("event_kind_field") or "kind") or "")


def _configured_url_parts(config: dict[str, Any], path: list[Any]) -> list[str]:
    return [str(item) for item in (_get_path(config, path) or []) if item]


def _parse_json(text: str) -> JsonDict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"raw": value}
    except Exception:
        return {"raw": text[:1000]}


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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
