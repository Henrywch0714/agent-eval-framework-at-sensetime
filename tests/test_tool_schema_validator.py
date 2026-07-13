from src.core.tool_schema_validator import validate_tool_schema


def _run_with_tool(tool_name, args):
    return {
        "run_id": "RUN-TEST",
        "observed": {
            "tool_chain": [
                {
                    "order": 1,
                    "tool_name": tool_name,
                    "args": args,
                }
            ]
        },
    }


def _registry():
    return {
        "tools": [
            {
                "name": "find_person_capture",
                "args": {
                    "search_type": {"type": "string", "required": True, "allowed": ["FACE", "PEDESTRIAN"]},
                    "image_url": {"type": "string", "required": False},
                    "appearance_visual_info": {"type": "string", "required": False},
                    "start_abscissa": {"type": "number", "required": False},
                    "start_ordinate": {"type": "number", "required": False},
                    "end_abscissa": {"type": "number", "required": False},
                    "end_ordinate": {"type": "number", "required": False},
                    "start_date": {"type": "string", "required": False, "format": "yyyyMMdd"},
                    "end_date": {"type": "string", "required": False, "format": "yyyyMMdd"},
                },
                "argument_rules": {
                    "required": ["search_type", "start_date", "end_date"],
                    "required_one_of": [["image_url", "appearance_visual_info"]],
                    "mutually_exclusive_groups": [["image_url", "appearance_visual_info"]],
                    "required_when": [
                        {
                            "when_arg_present": "image_url",
                            "required": ["start_abscissa", "start_ordinate", "end_abscissa", "end_ordinate"],
                        }
                    ],
                },
            }
        ]
    }


def test_tool_registry_accepts_valid_image_person_capture_call():
    run = _run_with_tool(
        "find_person_capture",
        {
            "search_type": "FACE",
            "image_url": "<image_ref>",
            "start_abscissa": 10,
            "start_ordinate": 20,
            "end_abscissa": 80,
            "end_ordinate": 120,
            "start_date": "20260706",
            "end_date": "20260709",
        },
    )

    result = validate_tool_schema(run, _registry(), {})

    assert result.score == 100
    assert result.failure_types == []


def test_tool_registry_flags_mutually_exclusive_image_and_text_args():
    run = _run_with_tool(
        "find_person_capture",
        {
            "search_type": "FACE",
            "image_url": "<image_ref>",
            "appearance_visual_info": "绿色衣服男人",
            "start_date": "20260706",
            "end_date": "20260709",
        },
    )

    result = validate_tool_schema(run, _registry(), {})

    assert result.score < 100
    assert "mutually_exclusive_args" in result.failure_types
    assert "missing_required_when" in result.failure_types
