from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


JUDGED_DIMENSIONS = ["task_understanding", "planning"]
DEFAULT_BLEND = 0.80
VERDICT_SCORES = {
    "excellent": 95,
    "good": 85,
    "partial": 60,
    "poor": 30,
    "fail": 0,
}


@dataclass
class LLMAssistantConfig:
    enabled: bool = False
    model: str = ""
    base_url: str = ""
    api_key_env: str = "SITE_AGENT_EVAL_LLM_API_KEY"
    blend: float = DEFAULT_BLEND
    timeout_seconds: int = 60
    verify_ssl: bool = True

    @property
    def api_key_present(self) -> bool:
        return bool(os.environ.get(self.api_key_env))

    def metadata(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "assistant_modules": ["judge", "summary"] if self.enabled else [],
            "model": self.model if self.enabled else "",
            "base_url": self.base_url if self.enabled else "",
            "api_key_env": self.api_key_env if self.enabled else "",
            "api_key_present": self.api_key_present if self.enabled else False,
            "judged_dimensions": JUDGED_DIMENSIONS if self.enabled else [],
            "dimension_blend": self.blend if self.enabled else 0,
            "verify_ssl": self.verify_ssl if self.enabled else True,
        }


def build_llm_assistant_config(
    enabled: bool = False,
    model: str = "",
    base_url: str = "",
    api_key_env: str = "SITE_AGENT_EVAL_LLM_API_KEY",
    blend: float = DEFAULT_BLEND,
) -> LLMAssistantConfig:
    env_enabled = os.environ.get("SITE_AGENT_EVAL_LLM_ASSISTANT", "").lower() in {"1", "true", "yes", "on"}
    resolved_enabled = enabled or env_enabled
    resolved_model = model or os.environ.get("SITE_AGENT_EVAL_LLM_MODEL", "")
    resolved_base_url = (base_url or os.environ.get("SITE_AGENT_EVAL_LLM_BASE_URL", "")).rstrip("/")
    resolved_api_key_env = api_key_env or os.environ.get("SITE_AGENT_EVAL_LLM_API_KEY_ENV", "SITE_AGENT_EVAL_LLM_API_KEY")
    verify_ssl = os.environ.get("SITE_AGENT_EVAL_LLM_VERIFY_SSL", "1").lower() not in {"0", "false", "no", "off"}
    return LLMAssistantConfig(
        enabled=resolved_enabled,
        model=resolved_model,
        base_url=resolved_base_url,
        api_key_env=resolved_api_key_env,
        blend=max(0.0, min(1.0, blend)),
        verify_ssl=verify_ssl,
    )


def judge_case(case: dict[str, Any], run: dict[str, Any] | None, profile: dict[str, Any] | None, config: LLMAssistantConfig) -> dict[str, Any] | None:
    if not config.enabled:
        return None
    base = config.metadata()
    if run is None:
        return {**base, "available": False, "error": "case_not_observed"}
    if not config.model or not config.base_url:
        return {**base, "available": False, "error": "missing_model_or_base_url"}
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        return {**base, "available": False, "error": f"missing_api_key_env:{config.api_key_env}"}

    prompt_payload = _judge_payload(case, run, profile or {})
    try:
        content = _chat_completion(
            base_url=config.base_url,
            api_key=api_key,
            model=config.model,
            payload=prompt_payload,
            timeout_seconds=config.timeout_seconds,
            verify_ssl=config.verify_ssl,
        )
        parsed = _parse_json_object(content)
    except Exception as exc:  # keep evaluation deterministic even if the remote judge fails
        return {**base, "available": False, "error": str(exc)[:300]}

    safe_dimensions, calibration = _normalize_judge_output(parsed if isinstance(parsed, dict) else {})
    return {
        **base,
        "available": bool(safe_dimensions),
        "dimension_scores": safe_dimensions,
        "score_scale": calibration["score_scale"],
        "raw_dimension_scores": calibration["raw_dimension_scores"],
        "dimension_verdicts": calibration["dimension_verdicts"],
        "calibration_notes": calibration["calibration_notes"],
        "rationale": str(parsed.get("rationale") or "")[:1000] if isinstance(parsed, dict) else "",
        "warnings": [str(item)[:200] for item in parsed.get("warnings", [])[:5]] if isinstance(parsed.get("warnings"), list) else [],
    }


def summarize_deductions(payload: dict[str, Any], config: LLMAssistantConfig) -> dict[str, Any]:
    """Generate a one-sentence deduction summary with the same LLM endpoint.

    This is intentionally presentation-only: it never changes scores, failure
    types, pass/fail state, or deterministic deduction items.
    """
    base = {
        **config.metadata(),
        "purpose": "one_sentence_deduction_summary",
    }
    if not config.enabled:
        return {**base, "available": False, "error": "llm_assistant_disabled", "output": ""}
    if not config.model or not config.base_url:
        return {**base, "available": False, "error": "missing_model_or_base_url", "output": ""}
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        return {**base, "available": False, "error": f"missing_api_key_env:{config.api_key_env}", "output": ""}

    try:
        content = _chat_completion(
            base_url=config.base_url,
            api_key=api_key,
            model=config.model,
            payload=_deduction_summary_payload(payload),
            timeout_seconds=config.timeout_seconds,
            verify_ssl=config.verify_ssl,
            system_prompt=(
                "You are a concise report assistant for Agent evaluation results. "
                "Write only JSON. Do not change scores or invent new failures."
            ),
        )
        parsed = _parse_json_object(content)
    except Exception as exc:
        return {**base, "available": False, "error": str(exc)[:300], "output": ""}

    summary = _clean_one_sentence(parsed.get("summary") or parsed.get("output") or parsed.get("text") or "")
    if not summary:
        return {**base, "available": False, "error": "empty_summary", "output": ""}
    return {
        **base,
        "available": True,
        "input": payload,
        "output": summary,
    }


def _judge_payload(case: dict[str, Any], run: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    observed = run.get("observed") or {}
    expected = case.get("expected") or {}
    tool_registry = profile.get("tool_registry") or {}
    return {
        "case_id": case.get("id"),
        "task_type": case.get("task_type"),
        "expected_user_task": case.get("user_task"),
        "actual_user_task_raw": run.get("user_task"),
        "expected_understanding": expected.get("understanding") or {},
        "expected_plan": expected.get("tool_plan") or expected.get("tool_chain") or {},
        "observed_explicit_plan_raw": observed.get("explicit_plan") or [],
        "tool_registry_summary": _tool_registry_summary(tool_registry),
        "judge_instructions": {
            "task_understanding": "Score semantic alignment between expected_user_task/expected_understanding and actual_user_task_raw. Do not use any normalized observed understanding field; it is intentionally not provided. Allow synonyms and paraphrases, but penalize missing core slots in the raw user request.",
            "planning": "Score only observed_explicit_plan_raw against expected_plan. Do not infer planning quality from later tool calls or normalized tool chains. If no explicit plan exists, score planning conservatively as missing or weak even if later tools were correct.",
            "score_scale": "Scores MUST be integers from 0 to 100. Do not use 0-1 or 0-5 scores. If a dimension is fully aligned, use 90-100. Mostly aligned with minor redundancy is 75-89. Partial is 50-74. Mostly wrong is 20-49. Missing or opposite is 0-19.",
            "verdict_values": ["excellent", "good", "partial", "poor", "fail"],
            "output": {
                "dimension_scores": {"task_understanding": "integer 0-100", "planning": "integer 0-100"},
                "dimension_verdicts": {"task_understanding": "excellent|good|partial|poor|fail", "planning": "excellent|good|partial|poor|fail"},
                "dimension_reasons": {"task_understanding": "short reason", "planning": "short reason"},
                "rationale": "one concise overall explanation",
                "warnings": ["optional warning strings"],
            },
        },
    }


def _tool_registry_summary(tool_registry: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for tool in (tool_registry.get("tools") or [])[:50]:
        out.append(
            {
                "name": tool.get("name"),
                "display_name": tool.get("display_name"),
                "category": tool.get("category"),
                "legacy_aliases": tool.get("legacy_aliases") or [],
            }
        )
    return out


def _deduction_summary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "根据结构化扣分项写一句中文总结，最多 45 个汉字。",
        "constraints": [
            "只总结已有扣分点，不新增事实。",
            "不要改变分数、通过状态或 failure_type。",
            "如果已经通过，也要说明主要扣分集中在哪里。",
        ],
        "data": payload,
        "output_schema": {"summary": "one Chinese sentence"},
    }


def _clean_one_sentence(text: Any) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    for sep in ["。", "！", "？", ".", "!", "?"]:
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0] + sep
            break
    return cleaned[:90]


def _chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    verify_ssl: bool,
    system_prompt: str | None = None,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt
                or (
                    "You are a strict but lightweight evaluator for website Agent traces. "
                    "Judge semantic equivalence for only the requested dimensions. "
                    "Do not evaluate law-enforcement correctness or real-world facts. "
                    "Return JSON only. Use 0-100 integer scores, never 0-5 scores."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0,
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=_ssl_context(verify_ssl)) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM judge HTTP {exc.code}: {detail}") from exc
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return str(message.get("content") or "")


def _normalize_judge_output(parsed: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    raw_scores = _first_dict(parsed, ["dimension_scores", "dimension_scores_0_to_100", "scores"])
    verdicts = _first_dict(parsed, ["dimension_verdicts", "verdicts"])
    numeric_values = [
        float(raw_scores.get(key))
        for key in JUDGED_DIMENSIONS
        if isinstance(raw_scores.get(key), (int, float))
    ]
    numeric_scale = _numeric_scale(numeric_values)
    calibration_notes = []
    safe_dimensions: dict[str, int] = {}
    normalized_verdicts: dict[str, str] = {}
    raw_dimension_scores: dict[str, Any] = {}

    for key in JUDGED_DIMENSIONS:
        raw_value = raw_scores.get(key)
        verdict = _normalize_verdict(verdicts.get(key))
        if verdict:
            normalized_verdicts[key] = verdict
        if isinstance(raw_value, (int, float)):
            raw_dimension_scores[key] = raw_value
            score = _scale_numeric_score(float(raw_value), numeric_scale)
        elif verdict:
            score = VERDICT_SCORES[verdict]
            calibration_notes.append(f"{key}: score_missing_used_verdict={verdict}")
        else:
            continue

        if verdict:
            verdict_score = VERDICT_SCORES[verdict]
            if abs(score - verdict_score) > 35:
                calibration_notes.append(f"{key}: numeric_score={score} contradicted verdict={verdict}; used {verdict_score}")
                score = verdict_score
        safe_dimensions[key] = max(0, min(100, round(score)))

    score_scale = numeric_scale
    if any("contradicted verdict" in note or "score_missing" in note for note in calibration_notes):
        score_scale = f"{numeric_scale}+verdict_calibrated"
    return safe_dimensions, {
        "score_scale": score_scale,
        "raw_dimension_scores": raw_dimension_scores,
        "dimension_verdicts": normalized_verdicts,
        "calibration_notes": calibration_notes,
    }


def _first_dict(parsed: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    for key in keys:
        value = parsed.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _numeric_scale(values: list[float]) -> str:
    if not values:
        return "verdict_only"
    maximum = max(values)
    if maximum <= 1:
        return "0-1_scaled_to_100"
    if maximum <= 5:
        return "0-5_scaled_to_100"
    return "0-100"


def _scale_numeric_score(value: float, scale: str) -> int:
    if scale == "0-1_scaled_to_100":
        return round(value * 100)
    if scale == "0-5_scaled_to_100":
        return round(value * 20)
    return round(value)


def _normalize_verdict(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "pass": "good",
        "ok": "good",
        "mostly_aligned": "good",
        "fully_aligned": "excellent",
        "full": "excellent",
        "excellent": "excellent",
        "good": "good",
        "partial": "partial",
        "partially_aligned": "partial",
        "poor": "poor",
        "bad": "poor",
        "fail": "fail",
        "failed": "fail",
        "missing": "fail",
    }
    return aliases.get(text, text if text in VERDICT_SCORES else "")


def _ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if not verify_ssl:
        return ssl._create_unverified_context()
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("LLM judge response did not contain JSON")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM judge response JSON was not an object")
    return value
