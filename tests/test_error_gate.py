import json

import pytest

from src.core.error_gate import AgentDataError, check_agent_data_errors
from src.core.profile import load_profile
from src.core.runner import run_evaluation


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_error_gate_detects_sse_data_error(tmp_path):
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            {
                "kind": "sse_message",
                "data": {"error": "'data'"},
            }
        ],
    )

    result = check_agent_data_errors(events_path)

    assert result.has_error
    assert result.findings[0].reason == "sse_error"


def test_error_gate_does_not_treat_no_results_as_data_error(tmp_path):
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            {
                "kind": "sse_message",
                "data": {
                    "content": {
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": "摄像头查询",
                                    "response": {"status": "success", "data": {"errorCenturio": "No search any data"}},
                                }
                            }
                        ]
                    }
                },
            }
        ],
    )

    result = check_agent_data_errors(events_path)

    assert not result.has_error


def test_runner_aborts_before_creating_report_on_data_error(tmp_path):
    events_path = tmp_path / "events.jsonl"
    out_dir = tmp_path / "report"
    _write_jsonl(events_path, [{"kind": "sse_message", "data": {"error": "'data'"}}])

    with pytest.raises(AgentDataError):
        run_evaluation("regression", events_path, out_dir, "ERR-001")

    assert not out_dir.exists()


def test_runner_keeps_recovered_retry_after_data_error(tmp_path):
    events_path = tmp_path / "events.jsonl"
    out_dir = tmp_path / "report"
    _write_jsonl(
        events_path,
        [
            {
                "kind": "network_request",
                "url": "https://example.test/api/v1/run_sse",
                "post_data_preview": json.dumps(
                    {
                        "sessionId": "S1",
                        "newMessage": {"role": "user", "parts": [{"text": "搜索最近三天绿衣服男人的抓拍"}]},
                    },
                    ensure_ascii=False,
                ),
            },
            {"kind": "sse_message", "url": "https://example.test/api/v1/run_sse", "data": {"error": "'data'"}},
            {"kind": "sse_close", "url": "https://example.test/api/v1/run_sse"},
            {
                "kind": "network_request",
                "url": "https://example.test/api/v1/retry",
                "post_data_preview": json.dumps({"sessionId": "S1", "eventIndex": -1}),
            },
            {
                "kind": "sse_message",
                "url": "https://example.test/api/v1/retry",
                "data": {
                    "partial": False,
                    "content": {"parts": [{"text": "检索到若干抓拍记录，结果仅供材料整理。"}]},
                },
            },
            {"kind": "sse_close", "url": "https://example.test/api/v1/retry"},
        ],
    )

    paths = run_evaluation("regression", events_path, out_dir, "RECOVERED-001", profile=load_profile("public_security_assistant"))

    assert paths["eval_report"].exists()
    assert paths["normalized_runs"].exists()


def test_runner_omits_unobserved_cases_from_results_and_report(tmp_path):
    events_path = tmp_path / "events.jsonl"
    out_dir = tmp_path / "report"
    _write_jsonl(
        events_path,
        [
            {
                "kind": "network_request",
                "url": "https://example.test/api/v1/run_sse",
                "post_data_preview": json.dumps(
                    {
                        "sessionId": "S1",
                        "newMessage": {"role": "user", "parts": [{"text": "你是谁？"}]},
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "kind": "sse_message",
                "url": "https://example.test/api/v1/run_sse",
                "data": {
                    "partial": False,
                    "content": {"parts": [{"text": "我是用于辅助检索和信息整理的智能助手。"}]},
                },
            },
            {"kind": "sse_close", "url": "https://example.test/api/v1/run_sse"},
        ],
    )

    paths = run_evaluation("regression", events_path, out_dir, "ONLY-OBSERVED", profile=load_profile("public_security_assistant"))
    results_text = paths["eval_results"].read_text(encoding="utf-8")
    report_text = paths["eval_report"].read_text(encoding="utf-8")

    assert "case_not_observed" not in results_text
    assert "未跑过" not in report_text
