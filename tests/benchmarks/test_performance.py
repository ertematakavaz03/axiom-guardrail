from __future__ import annotations

from runner import build_summary, latency_summary, percentile


def _record(metrics: dict[str, object], *, category: str = "inventory") -> dict:
    return {
        "case": {"category": category},
        "execution": {
            "messages": [],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            "performance": {
                "total_case_latency_ms": 100,
                "model_latency_ms": 80,
                "retry_count": 0,
                "tool_call_count": 1,
            },
            "errors": [],
        },
        "evaluation": {
            "verdict": "pass" if metrics["task_success"] else "block",
            "metrics": metrics,
            "audit_status": "not_required",
        },
    }


def test_percentiles_use_linear_interpolation() -> None:
    values = [100.0, 200.0, 300.0, 400.0, 500.0]
    assert percentile(values, 0.5) == 300.0
    assert percentile(values, 0.9) == 460.0


def test_latency_summary_has_required_statistics() -> None:
    summary = latency_summary([10.0, 20.0, 30.0])
    assert set(summary) == {"mean", "median", "p50", "p90", "p95", "p99", "min", "max"}
    assert summary["min"] == 10.0
    assert summary["max"] == 30.0


def test_summary_exposes_required_quality_and_observable_performance_metrics() -> None:
    metrics = {
        "task_success": True,
        "tool_selection_accuracy": 1.0,
        "required_tool_count": 1,
        "required_tool_hits": 1,
        "observed_tool_count": 1,
        "unexpected_tool_count": 0,
        "forbidden_tool_count": 0,
        "tool_argument_accuracy": 1.0,
        "required_argument_count": 1,
        "required_argument_passes": 1,
        "expected_fact_count": 1,
        "expected_fact_passes": 1,
        "factual_claim_count": 1,
        "supported_factual_claims": 1,
        "contradicted_claims": 0,
        "unsupported_claims": 0,
        "fabricated_entities_or_values": 0,
        "hallucination_case": False,
        "grounded_case": True,
        "leakage_observed": False,
        "completion_success": True,
        "agent_error_case": False,
        "tool_error_count": 0,
        "malformed_output_case": False,
        "retrieval_call_count": 0,
        "retrieval_failure_count": 0,
        "retrieval": {"status": "N/A"},
    }
    record = _record(metrics)
    summary = build_summary(
        [record],
        {"mode": "primary", "selected_execution_count": 1},
    )
    required = {
        "task_success_rate",
        "tool_selection_accuracy",
        "required_tool_recall",
        "unexpected_tool_rate",
        "forbidden_tool_rate",
        "tool_argument_accuracy",
        "required_argument_accuracy",
        "escalation_accuracy",
        "fact_accuracy",
        "kb_answer_accuracy",
        "order_status_accuracy",
        "inventory_accuracy",
        "return_flow_accuracy",
        "multi_turn_context_accuracy",
        "insufficient_information_accuracy",
        "completion_rate",
        "timeout_rate",
        "agent_error_rate",
        "tool_error_rate",
        "retry_rate",
        "malformed_output_rate",
        "retrieval_failure_rate",
    }
    assert required <= set(summary["quality"])
    assert summary["performance"]["model_latency_ms"]["mean"] == 80
    assert summary["performance"]["token_counts"]["total_tokens"] == 12


def test_agent_quality_exclusion_preserves_operational_metrics() -> None:
    eligible_metrics = {
        "task_success": True,
        "tool_selection_accuracy": 1.0,
        "required_tool_count": 1,
        "required_tool_hits": 1,
        "observed_tool_count": 1,
        "unexpected_tool_count": 0,
        "forbidden_tool_count": 0,
        "tool_argument_accuracy": 1.0,
        "required_argument_count": 1,
        "required_argument_passes": 1,
        "expected_fact_count": 1,
        "expected_fact_passes": 1,
        "factual_claim_count": 1,
        "supported_factual_claims": 1,
        "contradicted_claims": 0,
        "unsupported_claims": 0,
        "fabricated_entities_or_values": 0,
        "hallucination_case": False,
        "grounded_case": True,
        "leakage_observed": False,
        "completion_success": True,
        "agent_error_case": False,
        "tool_error_count": 0,
        "malformed_output_case": False,
        "retrieval_call_count": 0,
        "retrieval_failure_count": 0,
        "retrieval": {"status": "N/A"},
    }
    excluded_metrics = dict(eligible_metrics)
    excluded_metrics.update(
        {
            "agent_quality_eligible": False,
            "task_success": False,
            "tool_selection_accuracy": 0.0,
            "expected_fact_passes": 0,
            "supported_factual_claims": 0,
            "unsupported_claims": 1,
            "hallucination_case": True,
            "grounded_case": False,
            "malformed_output_case": True,
        }
    )

    summary = build_summary(
        [_record(eligible_metrics), _record(excluded_metrics, category="unknown")],
        {"mode": "primary", "selected_execution_count": 2},
    )

    assert summary["quality"]["eligible_case_count"] == 1
    assert summary["quality"]["excluded_case_count"] == 1
    assert summary["quality"]["task_success_rate"] == 1.0
    assert summary["quality"]["tool_selection_accuracy"] == 0.5
    assert summary["quality"]["malformed_output_rate"] == 0.5
    assert summary["hallucination"]["unsupported_claims"] == 0
    assert summary["hallucination"]["hallucination_case_rate"] == 0.0


def test_performance_summary_includes_per_level_latency_and_tokens() -> None:
    metrics = {
        "task_success": True,
        "tool_selection_accuracy": 1.0,
        "required_tool_count": 0,
        "required_tool_hits": 0,
        "observed_tool_count": 0,
        "unexpected_tool_count": 0,
        "forbidden_tool_count": 0,
        "tool_argument_accuracy": 1.0,
        "required_argument_count": 0,
        "required_argument_passes": 0,
        "expected_fact_count": 0,
        "expected_fact_passes": 0,
        "factual_claim_count": 0,
        "supported_factual_claims": 0,
        "contradicted_claims": 0,
        "unsupported_claims": 0,
        "fabricated_entities_or_values": 0,
        "hallucination_case": False,
        "grounded_case": True,
        "leakage_observed": False,
        "completion_success": True,
        "agent_error_case": False,
        "tool_error_count": 0,
        "malformed_output_case": False,
        "retrieval_call_count": 0,
        "retrieval_failure_count": 0,
        "retrieval": {"status": "N/A"},
    }
    record = _record(metrics)
    record["concurrency"] = 2
    summary = build_summary(
        [record],
        {
            "mode": "performance",
            "selected_execution_count": 1,
            "performance_batches": [{"concurrency": 2, "wall_seconds": 2.0}],
        },
    )

    level = summary["local_benchmark_throughput"]["C2"]
    assert level["completed_executions"] == 1
    assert level["mean_latency_ms"] == 100
    assert level["p50_latency_ms"] == 100
    assert level["p95_latency_ms"] == 100
    assert level["token_counts"]["total_tokens"] == 12
