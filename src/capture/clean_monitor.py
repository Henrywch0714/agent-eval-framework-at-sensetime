from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        self._fh = self.path.open("w", encoding="utf-8")

    def close(self) -> None:
        self._fh.close()

    def write(self, event: dict[str, Any], dedupe_key: str | None = None) -> None:
        if dedupe_key and dedupe_key in self._seen:
            return
        if dedupe_key:
            self._seen.add(dedupe_key)
        event.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        self._fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()


def capture_real_agent(
    url: str,
    events_path: Path,
    image_detail_limit: int = 10,
    user_data_dir: Path | None = None,
    capture_config: dict[str, Any] | None = None,
    normalizer_config: dict[str, Any] | None = None,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Real capture requires playwright. Install with: python3 -m pip install playwright") from exc

    user_data_dir = user_data_dir or Path("/tmp/site-agent-eval-chrome")
    capture_config = capture_config or {}
    normalizer_config = normalizer_config or {}
    events = JsonlWriter(events_path)

    def emit(event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind:
            print(f"[{str(kind).upper()}] {str(event.get('url') or '')[:120]}", flush=True)
        events.write(event)

    def on_request(request) -> None:
        req_url = request.url
        if not _is_relevant_url(req_url, capture_config):
            return
        emit(
            {
                "kind": "network_request",
                "url": req_url,
                "method": request.method,
                "resource_type": request.resource_type,
                "post_data_preview": _preview(request.post_data),
                "note": "headers/cookies/auth omitted",
            }
        )

    def on_response(response) -> None:
        req_url = response.url
        if not _is_relevant_url(req_url, capture_config):
            return
        request = response.request
        content_type = response.headers.get("content-type", "")
        emit(
            {
                "kind": "network_response_meta",
                "url": req_url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": response.status,
                "content_type": content_type,
            }
        )
        if "application/json" not in content_type:
            return
        try:
            payload = response.json()
        except Exception:
            return
        if _matches_any_url_part(req_url, capture_config.get("skill_inventory_url_parts") or []):
            emit({"kind": "skill_inventory", "url": req_url, "skills": _summarize_skills(payload)})
        if _matches_any_url_part(req_url, capture_config.get("oracle_summary_url_parts") or []):
            summary = _summarize_result_payload(payload, image_detail_limit=image_detail_limit, normalizer_config=normalizer_config)
            if summary:
                emit(
                    {
                        "kind": "multimodal_summary",
                        "url": req_url,
                        "method": request.method,
                        "status": response.status,
                        "summary": summary,
                    }
                )

    with sync_playwright() as p:
        executable = _chrome_path()
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            executable_path=executable,
            ignore_https_errors=True,
            args=["--ignore-certificate-errors"],
        )
        context.on("request", on_request)
        context.on("response", on_response)
        context.expose_function("__agentEvalEmit", emit)
        context.add_init_script(_fetch_capture_script(image_detail_limit=image_detail_limit))
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print("[LOGIN] If needed, log in manually in the opened Chrome window.")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("[CAPTURE STOP] Ctrl+C received. Closing browser and continuing to evaluation.")
        finally:
            try:
                context.close()
            except Exception:
                pass
            events.close()


def _chrome_path() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _is_relevant_url(url: str, capture_config: dict[str, Any]) -> bool:
    return _matches_any_url_part(url, capture_config.get("relevant_url_parts") or [])


def _matches_any_url_part(url: str, parts: list[str]) -> bool:
    return any(part in url for part in parts)


def _preview(value: Any, limit: int = 6000) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _summarize_skills(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    skills = ((payload.get("data") or {}).get("skills") or [])
    out = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        out.append(
            {
                "skill_name": skill.get("skill_name"),
                "enabled": skill.get("enabled"),
                "description": skill.get("description"),
            }
        )
    return out


def _summarize_result_payload(payload: Any, image_detail_limit: int, normalizer_config: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    rules = normalizer_config.get("oracle_evidence") or {}
    data = payload.get("data")
    results = _get_path(payload, rules.get("result_list_path") or [])
    if not isinstance(data, dict) or not isinstance(results, list):
        return None
    score_key = rules.get("score_key") or "score"
    scores = [float(item[score_key]) for item in results if isinstance(item, dict) and isinstance(item.get(score_key), (int, float))]
    points = Counter()
    top_k = []
    for idx, item in enumerate(results[:image_detail_limit], 1):
        if not isinstance(item, dict):
            continue
        point = _first_path(item, rules.get("point_ref_paths") or []) or "unknown"
        points[str(point)] += 1
        top_k.append(
            {
                "rank": idx,
                "evidence_id": _first_key(item, rules.get("evidence_id_keys") or []) or f"ev-{idx:03d}",
                "score": item.get(score_key),
                "capture_type": item.get(rules.get("capture_type_key") or "type"),
                "point_ref": point,
            }
        )
    return {
        "query_type": _get_path(payload, rules.get("query_type_path") or []),
        "page": _get_path(payload, rules.get("page_path") or []) or {},
        "result_count_observed": len(results),
        "score_stats": _score_stats(scores),
        "distinct_points": len(points) if points else None,
        "point_summary": [{"point_ref": key, "count": val} for key, val in points.most_common(5)],
        "top_k_refs": top_k,
    }


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


def _score_stats(scores: list[float]) -> dict[str, Any]:
    if not scores:
        return {}
    return {"count": len(scores), "min": min(scores), "avg": sum(scores) / len(scores), "max": max(scores)}


def _fetch_capture_script(image_detail_limit: int) -> str:
    return f"""
(() => {{
  if (window.__agentEvalFetchPatched) return;
  window.__agentEvalFetchPatched = true;

  const emit = (event) => {{
    try {{
      if (window.__agentEvalEmit) window.__agentEvalEmit(event);
    }} catch (e) {{}}
  }};

  const preview = (value, limit = 6000) => {{
    try {{
      if (value === undefined || value === null) return "";
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return text.length > limit ? text.slice(0, limit) + "...[truncated]" : text;
    }} catch (e) {{
      return String(value).slice(0, limit);
    }}
  }};

  const parseSseBlock = (block, url) => {{
    const lines = block.split(/\\r?\\n/);
    let eventName = "message";
    const dataLines = [];
    for (const line of lines) {{
      if (line.startsWith("event:")) eventName = line.slice(6).trim() || "message";
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }}
    if (!dataLines.length) return;
    const raw = dataLines.join("\\n");
    let data = raw;
    try {{ data = JSON.parse(raw); }} catch (e) {{}}
    emit({{kind: "sse_message", url, event: eventName, data, raw_size_chars: raw.length}});
  }};

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {{
    const input = args[0];
    const init = args[1] || {{}};
    const reqUrl = typeof input === "string" ? input : (input && input.url) || "";
    const method = init.method || (input && input.method) || "GET";
    const body = init.body || (input && input.body) || "";
    const response = await originalFetch(...args);
    const contentType = response.headers.get("content-type") || "";

    if (contentType.includes("text/event-stream") && response.body) {{
      const [captureStream, appStream] = response.body.tee();
      emit({{kind: "sse_open", url: response.url || reqUrl, method, status: response.status, content_type: contentType}});
      (async () => {{
        const reader = captureStream.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let chunks = 0;
        let chars = 0;
        try {{
          while (true) {{
            const {{value, done}} = await reader.read();
            if (done) break;
            chunks += 1;
            const text = decoder.decode(value, {{stream: true}});
            chars += text.length;
            buffer += text;
            const blocks = buffer.split(/\\n\\n+/);
            buffer = blocks.pop() || "";
            for (const block of blocks) parseSseBlock(block, response.url || reqUrl);
          }}
          if (buffer.trim()) parseSseBlock(buffer, response.url || reqUrl);
        }} finally {{
          emit({{kind: "sse_close", url: response.url || reqUrl, chunks, total_chars: chars}});
        }}
      }})();
      return new Response(appStream, {{
        status: response.status,
        statusText: response.statusText,
        headers: response.headers
      }});
    }}

    return response;
  }};
}})();
"""
