from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from .schema import JsonDict


def collect_evidence_from_tool_results(tool_items: list[JsonDict], config: dict[str, Any]) -> list[JsonDict]:
    rules = _aggregator_config(config)
    summaries = []
    for item in tool_items:
        if item.get("kind") != "result":
            continue
        response = item.get("response")
        if not isinstance(response, dict):
            continue
        for collector in _collectors(rules):
            if collector.get("collector_type", "result_set") != "result_set":
                continue
            summary = _result_set_summary_from_response(response, collector, source="tool_result")
            if summary:
                summaries.append(summary)
    return summaries


def aggregate_evidence(raw_summaries: list[JsonDict], config: dict[str, Any]) -> JsonDict:
    rules = _aggregator_config(config)
    if not raw_summaries:
        return _empty_evidence()
    sets = [_normalize_evidence_set(summary, rules) for summary in raw_summaries if isinstance(summary, dict)]
    sets = [item for item in sets if item.get("items") or item.get("page") or item.get("query_type")]
    if not sets:
        return _empty_evidence()
    if rules.get("dedupe", {}).get("mode", "page") == "page":
        sets = _dedupe_result_pages(sets)
    chosen = sets[-1]
    all_items = [item for summary in sets for item in summary.get("items") or []]
    total_count = _first_int([_get_path(summary, rules.get("total_count_path") or ["page", "total"]) for summary in sets])
    total_page = _first_int([_get_path(summary, rules.get("total_page_path") or ["page", "totalPage"]) for summary in sets])
    page_size = _first_int([_get_path(summary, rules.get("page_size_path") or ["page", "pageSize"]) for summary in sets])
    pages_observed = sorted({item.get("page") for item in all_items if isinstance(item.get("page"), int)})
    observed_count = len(all_items)
    coverage_ratio = round(observed_count / total_count, 4) if isinstance(total_count, int) and total_count > 0 else 0
    score_field = rules.get("score_field") or "score"
    score_stats = _numeric_stats([item[score_field] for item in all_items if isinstance(item.get(score_field), (int, float))])
    group_field = chosen.get("group_summary_field") or rules.get("group_summary_field")
    group_summary = _group_summary_from_items(all_items, group_field, rules.get("group_summary_output_key") or "point_ref")
    distinct_groups = len(group_summary) if group_summary else None
    time_stats = _range_stats([item[chosen.get("time_stats_field")] for item in all_items if chosen.get("time_stats_field") and isinstance(item.get(chosen.get("time_stats_field")), (int, float))])
    sample_items = _sample_items_for_report(all_items, rules)
    evidence_sets = [_compact_evidence_set(item) for item in sets]
    generic = {
        "schema_version": "oracle-evidence-v2",
        "evidence_set_count": len(sets),
        "evidence_sets": evidence_sets,
        "evidence_items": all_items,
        "evidence_stats": {
            "total_count": total_count,
            "observed_count": observed_count,
            "coverage_ratio": coverage_ratio,
            "score_stats": score_stats,
            "group_summary": group_summary,
            "distinct_groups": distinct_groups,
            "time_stats": time_stats,
            "pages_observed": pages_observed,
        },
        "query_type": chosen.get("query_type"),
        "total_count": total_count,
        "observed_count": observed_count,
        "coverage_ratio": coverage_ratio,
        "score_stats": score_stats,
        "distinct_points": distinct_groups,
        "point_summary": group_summary,
        "global_summary": {
            "total_count": total_count,
            "total_page": total_page,
            "page_size": page_size,
            "pages_observed": pages_observed,
            "coverage_ratio": coverage_ratio,
            "query_type": chosen.get("query_type"),
        },
        "observed_result_set": {
            "item_count": observed_count,
            "pages_observed": pages_observed,
            "items_per_page": _items_per_page(all_items),
            "score_stats": score_stats,
            "time_stats": time_stats,
            "point_summary": group_summary,
            "items": all_items,
        },
        "sample_summary": {
            "sample_mode": rules.get("sample", {}).get("mode", "first_middle_last"),
            "sample_size": len(sample_items),
            "sample_items": sample_items,
        },
    }
    # Backward-compatible alias for existing reports and cases.
    generic["image_result_sets"] = generic["evidence_set_count"]
    return generic


def _empty_evidence() -> JsonDict:
    return {
        "schema_version": "oracle-evidence-v2",
        "evidence_set_count": 0,
        "evidence_sets": [],
        "evidence_items": [],
        "evidence_stats": {},
        "image_result_sets": 0,
        "total_count": None,
        "observed_count": 0,
        "coverage_ratio": 0,
        "score_stats": {},
        "distinct_points": None,
        "global_summary": {},
        "observed_result_set": {"item_count": 0, "items": []},
        "sample_summary": {},
    }


def _aggregator_config(config: dict[str, Any]) -> dict[str, Any]:
    configured = config.get("evidence_aggregator")
    if isinstance(configured, dict):
        legacy = config.get("oracle_evidence") or {}
        if not legacy:
            return configured
        merged = dict(configured)
        for key in ["score_field", "group_summary_field", "time_stats_field", "item_fields"]:
            legacy_key = "point_summary_field" if key == "group_summary_field" else key
            if key not in merged and legacy.get(legacy_key) is not None:
                merged[key] = legacy.get(legacy_key)
        collectors = []
        for collector in _collectors(configured):
            collectors.append({**legacy, **collector})
        merged["collectors"] = collectors
        return merged
    legacy = config.get("oracle_evidence") or {}
    return {
        "schema_version": "evidence-aggregator-v1",
        "aggregator_type": "paged_result_set",
        "collectors": [{**legacy, "collector_type": "result_set"}],
        "score_field": "score",
        "group_summary_field": legacy.get("point_summary_field"),
        "time_stats_field": legacy.get("time_stats_field"),
        "item_fields": legacy.get("item_fields") or {},
        "sample": {"mode": "first_middle_last", "limit": 20},
        "dedupe": {"mode": "page"},
    }


def _collectors(rules: dict[str, Any]) -> list[JsonDict]:
    collectors = rules.get("collectors")
    if isinstance(collectors, list) and collectors:
        return [item for item in collectors if isinstance(item, dict)]
    return [rules]


def _result_set_summary_from_response(response: JsonDict, collector: JsonDict, source: str) -> JsonDict | None:
    query_type = _get_path(response, collector.get("query_type_path") or [])
    if collector.get("result_set_query_types") and query_type not in set(collector.get("result_set_query_types") or []):
        return None
    results = _get_path(response, collector.get("result_list_path") or [])
    if not isinstance(results, list):
        return None
    return _summarize_result_set(response, results, collector, source)


def _summarize_result_set(response: JsonDict, results: list[JsonDict], rules: dict[str, Any], source: str) -> JsonDict:
    page = _get_path(response, rules.get("page_path") or []) or {}
    items = [_evidence_item_from_result(item, idx, page, rules) for idx, item in enumerate(results, 1) if isinstance(item, dict)]
    score_field = rules.get("score_field") or "score"
    scores = [float(item[score_field]) for item in items if isinstance(item.get(score_field), (int, float))]
    group_field = rules.get("group_summary_field") or rules.get("point_summary_field")
    group_summary = _group_summary_from_items(items, group_field, rules.get("group_summary_output_key") or "point_ref")
    return {
        "source": source,
        "evidence_type": rules.get("evidence_type") or "result_set",
        "query_type": _get_path(response, rules.get("query_type_path") or []),
        "page": page,
        "result_count_observed": len(items),
        "score_stats": _numeric_stats(scores),
        "distinct_points": len(group_summary) if group_summary else None,
        "point_summary": group_summary[:5],
        "grounding_items": items,
        "items": items,
        "group_summary_field": group_field,
        "point_summary_field": group_field,
        "time_stats_field": rules.get("time_stats_field"),
    }


def _normalize_evidence_set(summary: JsonDict, rules: JsonDict) -> JsonDict:
    items = summary.get("items") or summary.get("grounding_items") or []
    normalized = {
        "source": summary.get("source"),
        "evidence_type": summary.get("evidence_type") or "result_set",
        "query_type": summary.get("query_type"),
        "page": summary.get("page") or {},
        "items": items,
        "group_summary_field": summary.get("group_summary_field") or summary.get("point_summary_field") or rules.get("group_summary_field"),
        "time_stats_field": summary.get("time_stats_field") or rules.get("time_stats_field"),
    }
    return normalized


def _evidence_item_from_result(item: JsonDict, local_index: int, page: JsonDict, rules: dict[str, Any]) -> JsonDict:
    page_no = _as_int(page.get("page")) or 1
    page_size = _as_int(page.get("pageSize")) or _as_int(page.get("page_size")) or 0
    start_index = _as_int(page.get("startIndex"))
    if start_index is None:
        start_index = (page_no - 1) * page_size if page_size else 0
    out: JsonDict = {
        "local_index": local_index,
        "global_index_estimate": start_index + local_index,
        "page": page_no,
        "page_size": page_size or None,
        "total_count": _as_int(page.get("total") or page.get("total_count")),
        "total_page": _as_int(page.get("totalPage") or page.get("total_page")),
        "scope": rules.get("item_scope") or "observed_result_item",
    }
    out.update(_extract_evidence_item_fields(item, rules))
    return out


def _extract_evidence_item_fields(item: JsonDict, rules: dict[str, Any]) -> JsonDict:
    specs = rules.get("item_fields") or {
        "score": {"path": [rules.get("score_key") or "score"]},
        "evidence_id": {"path_any": [[key] for key in rules.get("evidence_id_keys") or ["id"]]},
    }
    out: JsonDict = {}
    for field_name, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        value = _first_configured_path(item, spec)
        if value in (None, "", [], {}) and "default" in spec:
            value = spec.get("default")
        out[str(field_name)] = bool(value) if spec.get("as_bool") else value
    return out


def _first_configured_path(value: JsonDict, spec: JsonDict) -> Any:
    if isinstance(spec.get("path"), list):
        return _get_path(value, spec["path"])
    for path in spec.get("path_any") or []:
        found = _get_path(value, path)
        if found not in (None, "", [], {}):
            return found
    return None


def _dedupe_result_pages(summaries: list[JsonDict]) -> list[JsonDict]:
    pages: dict[tuple[Any, Any, Any], JsonDict] = {}
    no_page = []
    for summary in summaries:
        page = summary.get("page") or {}
        key = (summary.get("query_type"), page.get("page"), page.get("pageSize") or page.get("page_size"))
        if key[1] is None:
            no_page.append(summary)
            continue
        previous = pages.get(key)
        if previous is None or len(summary.get("items") or []) >= len(previous.get("items") or []):
            pages[key] = summary
    ordered = sorted(pages.values(), key=lambda item: ((item.get("page") or {}).get("page") or 0, (item.get("page") or {}).get("pageSize") or (item.get("page") or {}).get("page_size") or 0))
    return no_page + ordered


def _compact_evidence_set(summary: JsonDict) -> JsonDict:
    items = summary.get("items") or []
    return {
        "source": summary.get("source"),
        "evidence_type": summary.get("evidence_type"),
        "query_type": summary.get("query_type"),
        "page": summary.get("page") or {},
        "item_count": len(items),
        "score_stats": _numeric_stats([item["score"] for item in items if isinstance(item.get("score"), (int, float))]),
    }


def _group_summary_from_items(items: list[JsonDict], field_name: Any = None, output_key: str = "point_ref") -> list[JsonDict]:
    if not field_name:
        return []
    counts = Counter(str(item.get(str(field_name)) or "unknown") for item in items)
    return [{output_key: key, "count": value} for key, value in counts.most_common(20)]


def _items_per_page(items: list[JsonDict]) -> dict[str, int]:
    counts = Counter(str(item.get("page")) for item in items if item.get("page") is not None)
    return dict(sorted(counts.items(), key=lambda pair: int(pair[0]) if pair[0].isdigit() else 0))


def _sample_items_for_report(items: list[JsonDict], rules: dict[str, Any]) -> list[JsonDict]:
    sample = rules.get("sample") or {}
    limit = int(sample.get("limit") or 20)
    if len(items) <= limit:
        return items
    if sample.get("mode", "first_middle_last") != "first_middle_last":
        return items[:limit]
    head_count = max(1, min(8, limit // 2))
    tail_count = max(1, min(8, limit // 2))
    middle_count = max(0, limit - head_count - tail_count)
    head = items[:head_count]
    middle_start = max(head_count, len(items) // 2 - middle_count // 2)
    middle = items[middle_start : middle_start + middle_count]
    tail = items[-tail_count:]
    return head + middle + tail


def _first_int(values: list[Any]) -> int | None:
    for value in values:
        found = _as_int(value)
        if found is not None:
            return found
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _numeric_stats(values: list[float]) -> JsonDict:
    if not values:
        return {}
    return {"count": len(values), "min": min(values), "avg": statistics.mean(values), "max": max(values)}


def _range_stats(values: list[float]) -> JsonDict:
    if not values:
        return {}
    return {"count": len(values), "min": min(values), "max": max(values)}


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
