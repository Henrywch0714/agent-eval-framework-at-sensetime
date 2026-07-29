from src.core.llm_assistant import LLMAssistantConfig, _judge_payload, _normalize_judge_output, summarize_deductions


def test_llm_assistant_judge_keeps_valid_0_to_100_scores():
    scores, calibration = _normalize_judge_output(
        {
            "dimension_scores": {"task_understanding": 86, "planning": 72},
            "dimension_verdicts": {"task_understanding": "good", "planning": "partial"},
        }
    )

    assert scores == {"task_understanding": 86, "planning": 72}
    assert calibration["score_scale"] == "0-100"


def test_llm_assistant_judge_uses_verdict_when_small_scale_score_contradicts_reasoning():
    scores, calibration = _normalize_judge_output(
        {
            "dimension_scores": {"task_understanding": 2, "planning": 2},
            "dimension_verdicts": {"task_understanding": "excellent", "planning": "good"},
        }
    )

    assert scores == {"task_understanding": 95, "planning": 85}
    assert calibration["score_scale"] == "0-5_scaled_to_100+verdict_calibrated"
    assert len(calibration["calibration_notes"]) == 2


def test_llm_assistant_judge_can_score_from_verdicts_when_numbers_are_missing():
    scores, calibration = _normalize_judge_output(
        {
            "dimension_verdicts": {"task_understanding": "partial", "planning": "fail"},
        }
    )

    assert scores == {"task_understanding": 60, "planning": 0}
    assert calibration["score_scale"] == "verdict_only+verdict_calibrated"


def test_llm_assistant_judge_payload_uses_raw_user_task_and_raw_explicit_plan_only():
    case = {
        "id": "RAW-001",
        "user_task": "搜索最近三天绿衣服男人的抓拍。",
        "expected": {
            "understanding": {"target_type": "PERSON", "time_range_days": 3},
            "tool_plan": {"must_include": ["find_person_capture"]},
        },
    }
    run = {
        "user_task": "搜一下最近三天绿衣服男人的抓拍",
        "observed": {
            "task_understanding": {"intent": "normalized_should_not_be_sent"},
            "plan": ["legacy fallback"],
            "explicit_plan": [
                {
                    "plan": "查询最近三天出现的绿衣服男人抓拍。",
                    "steps": ["读取用户条件", "调用人员抓拍检索"],
                    "raw_args": {"plan": "查询最近三天出现的绿衣服男人抓拍。"},
                }
            ],
            "tool_chain": [{"order": 1, "tool_name": "find_person_capture"}],
        },
    }

    payload = _judge_payload(case, run, profile={})

    assert payload["actual_user_task_raw"] == "搜一下最近三天绿衣服男人的抓拍"
    assert payload["observed_explicit_plan_raw"][0]["plan"] == "查询最近三天出现的绿衣服男人抓拍。"
    assert "observed_task_understanding" not in payload
    assert "observed_tool_chain_for_context_only" not in payload
    assert "normalized_should_not_be_sent" not in str(payload)


def test_llm_assistant_judge_payload_does_not_fallback_to_tool_chain_as_plan():
    payload = _judge_payload(
        {"id": "NO-PLAN", "expected": {"tool_plan": {"must_include": ["find_person_capture"]}}},
        {
            "user_task": "搜一下最近三天抓拍",
            "observed": {
                "plan": ["find_person_capture"],
                "tool_chain": [{"order": 1, "tool_name": "find_person_capture"}],
            },
        },
        profile={},
    )

    assert payload["observed_explicit_plan_raw"] == []
    assert "find_person_capture" not in str(payload["observed_explicit_plan_raw"])


def test_deduction_summary_uses_same_llm_config(monkeypatch):
    monkeypatch.setenv("SITE_AGENT_EVAL_LLM_API_KEY", "test-key")

    def fake_chat_completion(**kwargs):
        assert kwargs["base_url"] == "https://example.test/v1"
        assert kwargs["model"] == "judge-model"
        assert kwargs["payload"]["task"].startswith("根据结构化扣分项")
        return '{"summary": "工具链正确，但显式计划缺失导致扣分。后续内容不应保留。"}'

    monkeypatch.setattr("src.core.llm_assistant._chat_completion", fake_chat_completion)

    result = summarize_deductions(
        {
            "case_id": "CASE-1",
            "score": 88,
            "top_deductions": [{"dimension": "planning", "reason": "未观察到显式计划"}],
        },
        LLMAssistantConfig(enabled=True, model="judge-model", base_url="https://example.test/v1"),
    )

    assert result["available"] is True
    assert result["model"] == "judge-model"
    assert result["output"] == "工具链正确，但显式计划缺失导致扣分。"
