from __future__ import annotations

import json
import os
import time
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
        # JSON bodies are captured in-page through fetch/XMLHttpRequest patches.
        # Reading them here is racy when the browser is closed after a long run.

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
        context.add_init_script(
            _fetch_capture_script(
                image_detail_limit=image_detail_limit,
                oracle_url_parts=capture_config.get("oracle_summary_url_parts") or [],
                oracle_config=(normalizer_config.get("oracle_evidence") or {}),
            )
        )
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


def _fetch_capture_script(image_detail_limit: int, oracle_url_parts: list[str] | None = None, oracle_config: dict[str, Any] | None = None) -> str:
    oracle_parts_json = json.dumps(oracle_url_parts or [], ensure_ascii=False)
    oracle_config_json = json.dumps(oracle_config or {}, ensure_ascii=False)
    return f"""
(() => {{
  if (window.__agentEvalFetchPatched) return;
  window.__agentEvalFetchPatched = true;
  const oracleUrlParts = {oracle_parts_json};
  const oracleConfig = {oracle_config_json};

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

  const isOracleUrl = (url) => oracleUrlParts.some((part) => String(url || "").includes(part));
  const getPath = (value, path) => {{
    let current = value;
    for (const key of path) {{
      if (!current || typeof current !== "object") return undefined;
      current = current[key];
    }}
    return current;
  }};
  const compactPage = (page) => {{
    const out = {{}};
    for (const key of ["page", "pageSize", "total", "totalPage", "hasNext", "offset", "startIndex", "endIndex"]) {{
      if (page && Object.prototype.hasOwnProperty.call(page, key)) out[key] = page[key];
    }}
    return out;
  }};
  const firstConfiguredPath = (item, spec) => {{
    if (!spec || typeof spec !== "object") return null;
    if (Array.isArray(spec.path)) {{
      const value = getPath(item, spec.path);
      if (value !== undefined && value !== null && value !== "") return value;
    }}
    if (Array.isArray(spec.path_any)) {{
      for (const path of spec.path_any) {{
        const value = getPath(item, path);
        if (value !== undefined && value !== null && value !== "") return value;
      }}
    }}
    if (Object.prototype.hasOwnProperty.call(spec, "default")) return spec.default;
    return null;
  }};
  const itemFields = (item) => {{
    const specs = oracleConfig.item_fields || {{}};
    const out = {{}};
    for (const [fieldName, spec] of Object.entries(specs)) {{
      const value = firstConfiguredPath(item, spec);
      out[fieldName] = spec && spec.as_bool ? Boolean(value) : value;
    }}
    return out;
  }};
  const resultSetSummary = (container, path) => {{
    const result = container.result;
    const page = container.page || {{}};
    const pageNo = Number(page.page || 1);
    const pageSize = Number(page.pageSize || (Array.isArray(result) ? result.length : 0));
    const startIndex = Number.isFinite(Number(page.startIndex)) ? Number(page.startIndex) : (pageNo - 1) * pageSize;
    const grounding = [];
    if (Array.isArray(result)) {{
      result.forEach((item, index) => {{
        if (!item || typeof item !== "object") return;
        grounding.push({{
          local_index: index + 1,
          global_index_estimate: startIndex + index + 1,
          page: pageNo,
          page_size: pageSize || null,
          total_count: Number.isFinite(Number(page.total)) ? Number(page.total) : null,
          total_page: Number.isFinite(Number(page.totalPage)) ? Number(page.totalPage) : null,
          scope: "observed_result_item",
          ...itemFields(item)
        }});
      }});
    }}
    return {{
      path,
      query_type: container.queryType,
      result_len: Array.isArray(result) ? result.length : 0,
      page: compactPage(page),
      pagination_observed: Number(page.total || 0) > (Array.isArray(result) ? result.length : 0),
      grounding_items: grounding,
      point_summary_field: oracleConfig.point_summary_field || null,
      time_stats_field: oracleConfig.time_stats_field || null
    }};
  }};
  const walkResultSets = (value, out, path) => {{
    if (value && typeof value === "object" && !Array.isArray(value)) {{
      if (Array.isArray(value.result) && value.page && typeof value.page === "object") {{
        out.push(resultSetSummary(value, path));
      }}
      for (const [key, child] of Object.entries(value)) walkResultSets(child, out, `${{path}}.${{key}}`);
    }} else if (Array.isArray(value)) {{
      value.slice(0, 50).forEach((child, index) => walkResultSets(child, out, `${{path}}[${{index}}]`));
    }}
  }};
  const summarizePayload = (payload) => {{
    const resultSets = [];
    walkResultSets(payload, resultSets, "$");
    return {{result_sets: resultSets.slice(0, 20), result_set_count: resultSets.length}};
  }};
  const emitJsonSummary = (kind, url, method, status, contentType, raw) => {{
    if (!isOracleUrl(url) || !String(contentType || "").includes("application/json") || !raw) return;
    let payload = null;
    try {{ payload = JSON.parse(raw); }} catch (e) {{}}
    if (!payload) return;
    emit({{
      kind,
      url,
      method,
      status,
      content_type: contentType,
      diagnostic: summarizePayload(payload)
    }});
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
    if (contentType.includes("application/json") && isOracleUrl(response.url || reqUrl)) {{
      try {{
        const clone = response.clone();
        clone.text().then((raw) => emitJsonSummary("fetch_json_response", response.url || reqUrl, method, response.status, contentType, raw)).catch(() => {{}});
      }} catch (e) {{}}
    }}

    return response;
  }};

  const originalXhrOpen = XMLHttpRequest.prototype.open;
  const originalXhrSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
    this.__agentEvalMethod = method || "GET";
    this.__agentEvalUrl = url || "";
    return originalXhrOpen.call(this, method, url, ...rest);
  }};
  XMLHttpRequest.prototype.send = function(body) {{
    const xhr = this;
    const method = xhr.__agentEvalMethod || "GET";
    const reqUrl = xhr.__agentEvalUrl || "";
    xhr.addEventListener("load", function() {{
      try {{
        const contentType = xhr.getResponseHeader("content-type") || "";
        const url = xhr.responseURL || reqUrl;
        emitJsonSummary("xhr_json_response", url, method, xhr.status, contentType, xhr.responseText || "");
      }} catch (e) {{}}
    }});
    return originalXhrSend.call(this, body);
  }};
}})();
"""
