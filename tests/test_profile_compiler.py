import json

from src.core.profile import load_profile, load_profile_cases
from src.core.profile_compiler import compile_profile
from src.core.profile_validator import validate_profile


def test_public_security_profile_uses_kits_without_losing_sections():
    profile = load_profile("public_security_assistant")

    assert profile["_compiled_profile"]["enabled"] is True
    assert [kit["kit_id"] for kit in profile["_compiled_profile"]["kits"]] == [
        "web_sse_agent",
        "tool_call_agent",
        "task_understanding_template",
        "multimodal_search_agent",
        "paged_search_grounding",
        "visual_paged_search_evidence",
        "safety_policy",
        "scoring_policy",
        "case_archetypes",
    ]
    assert profile["profile_validation"]["passed"] is True
    assert len(profile["tool_registry"]["tools"]) == 11
    assert len(profile["standard_answer_cases"]["cases"]) >= 5
    assert profile["normalizer_map"]["oracle_evidence"]["score_key"] == "score"
    assert profile["normalizer_map"]["oracle_evidence"]["evidence_id_keys"] == ["captureId", "objectId"]
    assert "camera_name" in profile["normalizer_map"]["oracle_evidence"]["item_fields"]
    assert profile["safety_policy"]["schema_version"] == "safety-policy-v1"
    assert profile["normalizer_map"]["safety_policy"]["schema_version"] == "safety-policy-v1"
    assert profile["scoring_policy"]["schema_version"] == "scoring-policy-v1"
    assert profile["scoring_policy"]["weights"]["evidence_grounding"] == 0.15
    task_rules = profile["normalizer_map"]["task_understanding"]
    assert "<img_url>" in task_rules["image_markers"]
    assert "抓拍" in task_rules["text_search_markers"]
    assert "学校附近" in task_rules["point_markers"]
    assert {"days": 3, "keywords": ["最近三天", "三天内", "3天", "最近3天", "last three days"]} in task_rules["time_range_markers"]
    assert "plate_number" in profile["normalizer_map"]["argument_compaction"]["keep_args"]
    assert "image_url" in profile["normalizer_map"]["argument_compaction"]["keep_args"]
    assert len(profile["normalizer_map"]["tool_result_summaries"]) == 4
    assert "tool_aliases" not in profile["normalizer_map"]
    assert profile["tool_aliases"]["人员抓拍检索"] == "find_person_capture"
    assert profile["tool_aliases"]["find_person_capture"] == "find_person_capture"
    assert profile["skill_map"]["find_person_capture"] == "capture-search"
    assert profile["skill_map"]["query_cameras"] == "camera-discovery"
    assert profile["skill_map"]["get_person_identity"] == "sensitive-identity-lookup"
    assert profile["skill_map"]["update_plan"] == "planning"
    person_capture = next(tool for tool in profile["tool_registry"]["tools"] if tool["name"] == "find_person_capture")
    assert "extends" not in person_capture
    assert {"image_url", "appearance_visual_info", "start_date", "camera_serials", "search_type"} <= set(person_capture["args"])
    assert person_capture["argument_rules"]["required_one_of"] == [["image_url", "appearance_visual_info"]]
    assert len((profile["tool_registry"]["data_flow_rules"] or {}).get("arg_sources") or []) == 3
    l1_case = next(case for case in load_profile_cases(profile, "l1") if case["id"] == "PS-L1-001")
    assert l1_case["expected"]["understanding"]["has_image_input"] is False
    assert l1_case["expected"]["answer"]["must_report_total_count_from_tool"] is True
    assert l1_case["expected"]["answer"]["must_not_confirm_identity"] is True
    assert "inherits_archetype" not in l1_case
    l2_cases = load_profile_cases(profile, "l2")
    image_case = next(case for case in l2_cases if case["id"] == "PS-L2-001")
    point_case = next(case for case in l2_cases if case["id"] == "PS-L2-002")
    previous_result_case = next(case for case in l2_cases if case["id"] == "PS-L2-004")
    assert image_case["expected"]["understanding"]["has_image_input"] is True
    assert point_case["expected"]["answer"]["must_separate_camera_count_and_capture_count"] is True
    assert previous_result_case["expected"]["understanding"]["has_image_input"] is False
    response_claims = profile["normalizer_map"]["response_claims"]
    assert [item["claim_type"] for item in response_claims["claim_extractors"]] == [
        "total_count",
        "distinct_points",
        "high_confidence_count",
    ]
    assert {item.get("claim_type") for item in response_claims["grounding_rules"]} >= {
        "total_count",
        "distinct_points",
        "high_confidence_count",
        "identity_conclusion",
    }


def test_profile_compiler_keeps_local_sections_as_overrides(tmp_path):
    kit_dir = tmp_path / "profile_kits"
    kit_path = kit_dir / "generic_search" / "kit.json"
    kit_path.parent.mkdir(parents=True)
    kit_path.write_text(
        json.dumps(
            {
                "kit_id": "generic_search",
                "normalizer_map": {
                    "oracle_evidence": {
                        "score_key": "generic_score",
                        "evidence_id_keys": ["generic_id"],
                    }
                },
                "tool_registry": {
                    "tools": [
                        {
                            "name": "search",
                            "display_name": "Generic Search",
                            "args": {"query": {"type": "string"}},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    compiled = compile_profile(
        {"profile_id": "demo", "uses": ["generic_search"]},
        sections={
            "normalizer_map": {
                "oracle_evidence": {
                    "score_key": "domain_score",
                    "result_list_path": ["items"],
                }
            },
            "tool_registry": {
                "tools": [
                    {
                        "name": "search",
                        "display_name": "Domain Search",
                        "args": {"query": {"type": "string"}, "limit": {"type": "number"}},
                    }
                ]
            },
        },
        kit_dir=kit_dir,
    )

    validation = validate_profile(compiled)

    assert validation.passed is True
    assert compiled["normalizer_map"]["oracle_evidence"]["score_key"] == "domain_score"
    assert compiled["normalizer_map"]["oracle_evidence"]["evidence_id_keys"] == ["generic_id"]
    assert compiled["normalizer_map"]["oracle_evidence"]["result_list_path"] == ["items"]
    assert compiled["tool_registry"]["tools"][0]["display_name"] == "Domain Search"
    assert "limit" in compiled["tool_registry"]["tools"][0]["args"]


def test_profile_compiler_merges_response_claim_rule_lists(tmp_path):
    kit_dir = tmp_path / "profile_kits"
    kit_path = kit_dir / "claim_grounding" / "kit.json"
    kit_path.parent.mkdir(parents=True)
    kit_path.write_text(
        json.dumps(
            {
                "kit_id": "claim_grounding",
                "normalizer_map": {
                    "response_claims": {
                        "claim_extractors": [
                            {
                                "claim_type": "total_count",
                                "output_key": "total_count",
                                "method": "number_after_prefix",
                                "prefixes": ["found"],
                            }
                        ],
                        "grounding_rules": [
                            {
                                "claim_type": "total_count",
                                "evidence_path": ["total_count"],
                                "comparator": "equals",
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    compiled = compile_profile(
        {"profile_id": "demo", "uses": ["claim_grounding"]},
        sections={
            "normalizer_map": {
                "response_claims": {
                    "claim_extractors": [
                        {
                            "claim_type": "total_count",
                            "prefixes": ["共检索到"],
                        }
                    ],
                    "grounding_rules": [
                        {
                            "claim_type": "identity_conclusion",
                            "claim_key": "contains_identity_conclusion",
                            "comparator": "must_not_exist",
                        }
                    ],
                }
            }
        },
        kit_dir=kit_dir,
    )

    claims = compiled["normalizer_map"]["response_claims"]
    assert claims["claim_extractors"] == [
        {
            "claim_type": "total_count",
            "output_key": "total_count",
            "method": "number_after_prefix",
            "prefixes": ["共检索到"],
        }
    ]
    assert [rule["claim_type"] for rule in claims["grounding_rules"]] == [
        "total_count",
        "identity_conclusion",
    ]


def test_profile_compiler_merges_task_understanding_template_lists(tmp_path):
    kit_dir = tmp_path / "profile_kits"
    kit_path = kit_dir / "task_understanding_template" / "kit.json"
    kit_path.parent.mkdir(parents=True)
    kit_path.write_text(
        json.dumps(
            {
                "kit_id": "task_understanding_template",
                "normalizer_map": {
                    "task_understanding": {
                        "text_search_markers": ["搜索", "查询"],
                        "point_markers": ["附近"],
                        "time_range_markers": [
                            {"days": 3, "keywords": ["最近三天"]},
                            {"days": 7, "keywords": ["最近七天"]},
                        ],
                        "target_type_text_keywords": [
                            {"target_type": "PERSON", "keywords": ["人"]},
                            {"target_type": "VEHICLE", "keywords": ["车"]},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    compiled = compile_profile(
        {"profile_id": "demo", "uses": ["task_understanding_template"]},
        sections={
            "normalizer_map": {
                "task_understanding": {
                    "text_search_markers": ["抓拍"],
                    "point_markers": ["附近", "学校附近"],
                    "time_range_markers": [
                        {"days": 3, "keywords": ["三天内"]},
                    ],
                    "target_type_text_keywords": [
                        {"target_type": "PERSON", "keywords": ["人员", "男人"]},
                    ],
                }
            }
        },
        kit_dir=kit_dir,
    )

    rules = compiled["normalizer_map"]["task_understanding"]
    assert rules["text_search_markers"] == ["搜索", "查询", "抓拍"]
    assert rules["point_markers"] == ["附近", "学校附近"]
    assert rules["time_range_markers"] == [
        {"days": 3, "keywords": ["三天内"]},
        {"days": 7, "keywords": ["最近七天"]},
    ]
    assert rules["target_type_text_keywords"] == [
        {"target_type": "PERSON", "keywords": ["人员", "男人"]},
        {"target_type": "VEHICLE", "keywords": ["车"]},
    ]


def test_profile_compiler_expands_tool_archetypes(tmp_path):
    kit_dir = tmp_path / "profile_kits"
    kit_path = kit_dir / "toolkit" / "kit.json"
    kit_path.parent.mkdir(parents=True)
    kit_path.write_text(
        json.dumps(
            {
                "kit_id": "toolkit",
                "tool_registry": {
                    "arg_schema_fragments": {
                        "time_range": {
                            "start_date": {"type": "string", "required": False},
                            "end_date": {"type": "string", "required": False},
                        }
                    },
                    "tool_archetypes": {
                        "search_tool": {
                            "category": "search",
                            "arg_fragments": ["time_range"],
                            "argument_rules": {"required_one_of": [["query", "image_url"]]},
                        }
                    },
                    "tools": [],
                },
            }
        ),
        encoding="utf-8",
    )

    compiled = compile_profile(
        {"profile_id": "demo", "uses": ["toolkit"]},
        sections={
            "tool_registry": {
                "tools": [
                    {
                        "name": "domain_search",
                        "extends": ["search_tool"],
                        "args": {"query": {"type": "string", "required": True}},
                        "argument_rules": {"required": ["query"]},
                    }
                ]
            }
        },
        kit_dir=kit_dir,
    )

    tool = compiled["tool_registry"]["tools"][0]
    assert tool["category"] == "search"
    assert "extends" not in tool
    assert {"query", "start_date", "end_date"} <= set(tool["args"])
    assert tool["argument_rules"]["required_one_of"] == [["query", "image_url"]]
    assert tool["argument_rules"]["required"] == ["query"]
