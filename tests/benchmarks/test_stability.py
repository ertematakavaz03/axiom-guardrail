from __future__ import annotations

from stability import analyze


def _record(repeat: int, verdict: str = "pass", latency: int = 100) -> dict:
    return {
        "case": {"id": "case-1"},
        "repeat": repeat,
        "execution": {
            "tool_calls": [{"name": "lookup", "arguments": {"id": "1"}}],
            "performance": {"total_case_latency_ms": latency},
        },
        "evaluation": {
            "verdict": verdict,
            "metrics": {
                "expected_fact_passes": 1,
                "expected_fact_count": 1,
                "supported_factual_claims": 1,
                "contradicted_claims": 0,
                "unsupported_claims": 0,
                "fabricated_entities_or_values": 0,
                "grounded_case": True,
            },
        },
    }


def test_stability_analysis_keeps_repeats_separate_from_primary() -> None:
    result = analyze([_record(1, latency=90), _record(2), _record(3, latency=110)])
    assert result["execution_count"] == 3
    assert result["separate_from_primary_score"] is True
    assert result["consistency"] == {
        "verdict_consistency": 1.0,
        "tool_selection_consistency": 1.0,
        "argument_consistency": 1.0,
        "fact_consistency": 1.0,
        "hallucination_consistency": 1.0,
    }
