from src.core.data_flow_validator import validate_data_flow
from src.core.provenance import attach_provenance


def _registry():
    return {
        "tools": [
            {"name": "image_analysis", "category": "preprocess"},
            {"name": "query_cameras", "category": "camera_discovery"},
            {"name": "find_person_capture", "category": "capture_search"},
        ],
        "data_flow_rules": {
            "arg_sources": [
                {
                    "name": "image_analysis_bbox_to_capture_bbox",
                    "arg_name": "bbox",
                    "target_tool_categories": ["capture_search"],
                    "target_arg_keys": ["start_abscissa", "start_ordinate", "end_abscissa", "end_ordinate"],
                    "target_required_when_arg_present": "image_url",
                    "target_type_arg": "search_type",
                    "source_tool": "image_analysis",
                    "source_output_collection": "detected_targets",
                    "source_output_type": "detected_target",
                    "source_value_key": "bbox",
                    "source_type_key": "target_type",
                    "explicit_source_keys": ["bbox_source", "bbox_result_key", "source_result_key", "tool_result_key"],
                    "allowed_source_types_by_target_arg": {"FACE": ["FACE"], "*": ["FACE", "PEDESTRIAN", "VEHICLE"]},
                    "failure_type": "bbox_not_from_image_analysis",
                },
                {
                    "name": "query_cameras_to_capture_camera_serials",
                    "arg_name": "camera_serials",
                    "target_tool_categories": ["capture_search"],
                    "target_arg_keys": ["camera_serials"],
                    "source_tool": "query_cameras",
                    "source_output_collection": "camera_refs",
                    "source_output_type": "camera_ref",
                    "source_value_key": "camera_serial",
                    "source_result_list_key": "camera_serials",
                    "case_required_flags": ["camera_serials_should_come_from"],
                    "failure_type": "camera_serials_not_from_query",
                },
            ]
        },
    }


def _image_flow_run(search_bbox):
    return {
        "run_id": "RUN-FLOW",
        "observed": {
            "tool_chain": [
                {"order": 1, "tool_name": "image_analysis", "args": {"image_url": "<image_ref>"}},
                {
                    "order": 2,
                    "tool_name": "find_person_capture",
                    "args": {"search_type": "FACE", "image_url": "<image_ref>", **search_bbox},
                },
            ],
            "tool_results": [
                {
                    "after_order": 1,
                    "tool_name": "image_analysis",
                    "detected_targets": [
                        {
                            "target_type": "FACE",
                            "bbox": {
                                "start_abscissa": 10,
                                "start_ordinate": 20,
                                "end_abscissa": 80,
                                "end_ordinate": 120,
                            },
                        }
                    ],
                }
            ],
        },
    }


def test_data_flow_accepts_bbox_from_image_analysis():
    run = _image_flow_run(
        {
            "start_abscissa": 10,
            "start_ordinate": 20,
            "end_abscissa": 80,
            "end_ordinate": 120,
        }
    )

    result = validate_data_flow(run, {}, _registry())

    assert result.score == 100
    assert result.failure_types == []


def test_data_flow_flags_bbox_not_from_image_analysis():
    run = _image_flow_run(
        {
            "start_abscissa": 1,
            "start_ordinate": 2,
            "end_abscissa": 3,
            "end_ordinate": 4,
        }
    )

    result = validate_data_flow(run, {}, _registry())

    assert result.score == 0
    assert "bbox_not_from_image_analysis" in result.failure_types


def test_data_flow_requires_camera_serials_when_case_demands_camera_flow():
    run = {
        "run_id": "RUN-CAMERA-FLOW",
        "observed": {
            "tool_chain": [
                {"order": 1, "tool_name": "query_cameras", "args": {"label_name": "学校"}},
                {"order": 2, "tool_name": "find_person_capture", "args": {"appearance_visual_info": "红色外套女性"}},
            ],
            "tool_results": [
                {"after_order": 1, "tool_name": "query_cameras", "camera_serials": ["CAM-001", "CAM-002"]}
            ],
        },
    }
    case = {
        "expected": {
            "arguments": {
                "find_person_capture": {
                    "required_args": ["camera_serials"],
                    "camera_serials_should_come_from": "query_cameras",
                }
            }
        }
    }

    result = validate_data_flow(run, case, _registry())

    assert result.score == 0
    assert "camera_serials_not_from_query" in result.failure_types


def test_data_flow_accepts_explicit_bbox_source_key():
    tool_chain = [
        {"order": 1, "tool_name": "image_analysis", "args": {"image_url": "<image_ref>"}},
        {
            "order": 2,
            "tool_name": "find_person_capture",
            "args": {
                "search_type": "FACE",
                "image_url": "<image_ref>",
                "bbox_source": "RUN-KEY.tool_001.result.detected_targets[0]",
                "start_abscissa": 10,
                "start_ordinate": 20,
                "end_abscissa": 80,
                "end_ordinate": 120,
            },
        },
    ]
    tool_results = [
        {
            "after_order": 1,
            "tool_name": "image_analysis",
            "result_key": "RUN-KEY.tool_001.result",
            "detected_targets": [
                {
                    "result_key": "RUN-KEY.tool_001.result.detected_targets[0]",
                    "target_type": "FACE",
                    "bbox": {
                        "start_abscissa": 10,
                        "start_ordinate": 20,
                        "end_abscissa": 80,
                        "end_ordinate": 120,
                    },
                }
            ],
        }
    ]
    attach_provenance(tool_chain, tool_results, _registry())

    result = validate_data_flow({"observed": {"tool_chain": tool_chain, "tool_results": tool_results}}, {}, _registry())

    assert result.score == 100
    assert tool_chain[1]["arg_sources"][0]["mode"] == "explicit_key"


def test_data_flow_flags_explicit_bbox_source_with_wrong_target_type():
    tool_chain = [
        {"order": 1, "tool_name": "image_analysis", "args": {"image_url": "<image_ref>"}},
        {
            "order": 2,
            "tool_name": "find_person_capture",
            "args": {
                "search_type": "FACE",
                "image_url": "<image_ref>",
                "bbox_source": "RUN-KEY.tool_001.result.detected_targets[0]",
                "start_abscissa": 10,
                "start_ordinate": 20,
                "end_abscissa": 80,
                "end_ordinate": 120,
            },
        },
    ]
    tool_results = [
        {
            "after_order": 1,
            "tool_name": "image_analysis",
            "result_key": "RUN-KEY.tool_001.result",
            "detected_targets": [
                {
                    "result_key": "RUN-KEY.tool_001.result.detected_targets[0]",
                    "target_type": "VEHICLE",
                    "bbox": {
                        "start_abscissa": 10,
                        "start_ordinate": 20,
                        "end_abscissa": 80,
                        "end_ordinate": 120,
                    },
                }
            ],
        }
    ]
    attach_provenance(tool_chain, tool_results, _registry())

    result = validate_data_flow({"observed": {"tool_chain": tool_chain, "tool_results": tool_results}}, {}, _registry())

    assert result.score == 0
    assert "bbox_not_from_image_analysis" in result.failure_types


def test_data_flow_uses_registry_rules_without_domain_tool_names():
    registry = {
        "tools": [
            {"name": "detect_entity", "category": "preprocess"},
            {"name": "search_entity", "category": "entity_search"},
        ],
        "data_flow_rules": {
            "arg_sources": [
                {
                    "name": "generic_detection_to_search_region",
                    "arg_name": "region",
                    "target_tool_categories": ["entity_search"],
                    "target_arg_keys": ["left", "top", "right", "bottom"],
                    "target_required_when_arg_present": "image_ref",
                    "source_tool": "detect_entity",
                    "source_output_collection": "regions",
                    "source_output_type": "detected_region",
                    "source_value_key": "region",
                    "failure_type": "region_not_from_detection",
                }
            ]
        },
    }
    run = {
        "observed": {
            "tool_chain": [
                {"order": 1, "tool_name": "detect_entity", "args": {"image_ref": "<image_ref>"}},
                {
                    "order": 2,
                    "tool_name": "search_entity",
                    "args": {"image_ref": "<image_ref>", "left": 1, "top": 2, "right": 3, "bottom": 4},
                },
            ],
            "tool_results": [
                {
                    "after_order": 1,
                    "tool_name": "detect_entity",
                    "regions": [{"region": {"left": 1, "top": 2, "right": 3, "bottom": 4}}],
                }
            ],
        }
    }

    result = validate_data_flow(run, {}, registry)

    assert result.score == 100
