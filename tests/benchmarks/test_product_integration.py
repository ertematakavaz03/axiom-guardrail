from __future__ import annotations

from integrate import (
    AGENT_NAME,
    PROJECT_NAME,
    SUITE_NAME,
    numeric_usage,
    product_metrics,
)


def test_external_benchmark_uses_existing_axiom_hierarchy() -> None:
    assert PROJECT_NAME == "External Benchmarks"
    assert AGENT_NAME == "LangGraph Customer Support Agent"
    assert SUITE_NAME == "External LangGraph Support Benchmark v1"


def test_product_metrics_preserve_unavailable_values() -> None:
    summary = {
        "quality": {
            "task_success_rate": 0.75,
            "tool_selection_accuracy": 0.8,
            "tool_argument_accuracy": 0.9,
        },
        "performance": {
            "total_case_latency_ms": {"mean": 100.0, "p95": 150.0},
            "token_counts": {"total_tokens": 42},
        },
        "privacy_isolation": {"leakage_cases": 0},
        "retrieval": {
            "recall_at_1": 0.5,
            "recall_at_3": 1.0,
            "recall_at_5": 1.0,
            "mrr": 0.5,
            "ndcg": 0.75,
        },
        "hallucination": {"grounded_case_rate": 0.9, "unsupported_claim_rate": 0.1},
    }
    metrics = product_metrics(summary)
    assert metrics["task_success"] == 75.0
    assert metrics["citation_precision"] is None
    assert metrics["embedding_latency"] is None


def test_numeric_usage_falls_back_to_ollama_metadata() -> None:
    execution = {
        "usage": {"input_tokens": "[REDACTED]"},
        "messages": [
            {
                "model_metadata": {
                    "prompt_eval_count": 10,
                    "eval_count": 3,
                }
            }
        ],
    }
    assert numeric_usage(execution) == {
        "input_tokens": 10,
        "output_tokens": 3,
        "total_tokens": 13,
    }
