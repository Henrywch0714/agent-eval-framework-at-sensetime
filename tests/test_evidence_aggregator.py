from src.core.evidence_aggregator import aggregate_evidence


def test_evidence_aggregator_supports_text_retrieval_items():
    summaries = [
        {
            "source": "tool_result",
            "evidence_type": "text_retrieval",
            "query_type": "DOC_SEARCH",
            "items": [
                {"score": 0.82, "doc_id": "DOC-A", "token_count": 120},
                {"score": 0.91, "doc_id": "DOC-A", "token_count": 80},
                {"score": 0.74, "doc_id": "DOC-B", "token_count": 60},
            ],
            "group_summary_field": "doc_id",
        }
    ]

    oracle = aggregate_evidence(
        summaries,
        {
            "evidence_aggregator": {
                "aggregator_type": "generic_collection",
                "score_field": "score",
                "group_summary_field": "doc_id",
                "group_summary_output_key": "doc_ref",
                "sample": {"mode": "first", "limit": 2},
                "dedupe": {"mode": "none"},
            }
        },
    )

    assert oracle["evidence_set_count"] == 1
    assert oracle["observed_count"] == 3
    assert oracle["evidence_sets"][0]["evidence_type"] == "text_retrieval"
    assert oracle["evidence_stats"]["score_stats"]["max"] == 0.91
    assert oracle["evidence_stats"]["group_summary"] == [{"doc_ref": "DOC-A", "count": 2}, {"doc_ref": "DOC-B", "count": 1}]
    assert oracle["sample_summary"]["sample_size"] == 2
