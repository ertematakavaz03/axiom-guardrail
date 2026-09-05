from __future__ import annotations

import math
from collections import Counter
from typing import Any

from services.evaluators.models import EvaluationResult

HARD_BLOCKERS = {
    "FORBIDDEN_TOOL_CALLED",
    "UNAUTHORIZED_TOOL_ATTEMPT",
    "TOOL_CONFIRMATION_REQUIRED",
    "TOOL_POLICY_VIOLATION",
    "RAG_TENANT_SCOPE_VIOLATION",
}

# Lower values are more causally useful. Evaluator execution order must never determine the
# primary reason displayed to a developer.
REASON_CODE_PRIORITY = {
    # Security and policy decisions.
    "RAG_TENANT_SCOPE_VIOLATION": 0,
    "TOOL_CONFIRMATION_REQUIRED": 1,
    "UNAUTHORIZED_TOOL_ATTEMPT": 2,
    "TOOL_POLICY_VIOLATION": 3,
    "TOOL_CALL_LIMIT_EXCEEDED": 4,
    # Infrastructure and execution failures.
    "TOOL_TIMEOUT": 10,
    "QDRANT_TIMEOUT": 11,
    "QDRANT_UNAVAILABLE": 12,
    "EMBEDDING_FAILURE": 13,
    "RETRIEVAL_FAILURE": 14,
    "RERANKER_FAILURE": 15,
    "DOCUMENT_PARSING_ERROR": 16,
    "DOCUMENT_INGESTION_ERROR": 17,
    "AGENT_TIMEOUT": 18,
    "AGENT_CONNECTION_ERROR": 19,
    "AGENT_RESPONSE_VALIDATION_ERROR": 20,
    "CASE_TIMEOUT": 21,
    "INTERNAL_EXECUTION_ERROR": 22,
    "AGENT_EXECUTION_FAILED": 23,
    # RAG source authorization and freshness.
    "CITATION_OUT_OF_SCOPE": 24,
    "STALE_SOURCE_USED": 25,
    # Retrieval is upstream of answer grounding.
    "GOLD_EVIDENCE_NOT_RETRIEVED": 26,
    "WRONG_DOCUMENT_RETRIEVED": 27,
    # Tool selection. A forbidden call remains a hard blocker even though policy is primary.
    "FORBIDDEN_TOOL_CALLED": 30,
    "UNEXPECTED_TOOL_CALLED": 31,
    # Argument correctness, most specific first.
    "MISSING_REQUIRED_TOOL_ARGUMENT": 40,
    "INVALID_TOOL_ARGUMENT_SCHEMA": 41,
    "INCORRECT_TOOL_ARGUMENT": 42,
    # Evidence and grounding failures.
    "HALLUCINATED_CLAIM": 45,
    "UNSUPPORTED_CLAIM": 46,
    "WRONG_CITATION": 50,
    "CITATION_NOT_FOUND": 51,
    "CITATION_NOT_RETRIEVED": 52,
    "MISSING_CITATION": 53,
    # Secondary behavioral consequences.
    "EXPECTED_TOOL_NOT_CALLED": 60,
    "TASK_TOOL_OUTCOME_MISMATCH": 61,
    "TASK_OUTPUT_MISMATCH": 62,
    "LOW_RETRIEVAL_RECALL": 70,
    "TOKEN_BUDGET_EXCEEDED": 71,
}


def order_reason_codes(reason_codes: list[str]) -> list[str]:
    unique = list(dict.fromkeys(reason_codes))
    original_position = {reason: index for index, reason in enumerate(unique)}
    return sorted(
        unique,
        key=lambda reason: (REASON_CODE_PRIORITY.get(reason, 100), original_position[reason]),
    )


def decide_case_verdict(
    evaluations: list[EvaluationResult], severity: str, gate_policy: dict[str, Any]
) -> tuple[str, float, list[str]]:
    failed = [result for result in evaluations if not result.passed]
    reason_codes = order_reason_codes(
        [result.reason_code for result in failed if result.reason_code]
    )
    metric_pass = {
        metric: not any(result.metric == metric and not result.passed for result in evaluations)
        for metric in {result.metric for result in evaluations}
    }
    is_rag = any(
        result.metric.startswith("retrieval_") or result.metric == "groundedness"
        for result in evaluations
    )
    if is_rag:
        score = (
            20 * float(metric_pass.get("task_success", True))
            + 25 * float(metric_pass.get("retrieval_recall_at_5", True))
            + 15 * float(metric_pass.get("citation_exists", True))
            + 20 * float(metric_pass.get("citation_support", True))
            + 20 * float(metric_pass.get("groundedness", True))
        )
    else:
        score = (
            40 * float(metric_pass.get("task_success", True))
            + 15 * float(metric_pass.get("tool_selection", True))
            + 15 * float(metric_pass.get("tool_arguments", True))
            + 20 * float(metric_pass.get("authorization", True))
            + 5 * float(metric_pass.get("latency", True))
            + 5 * float(metric_pass.get("token_usage", True))
        )
    if set(reason_codes) & HARD_BLOCKERS:
        return "block", score, reason_codes
    block_severities = set(gate_policy.get("block_severities", ["critical"]))
    if failed and severity in block_severities:
        return "block", score, reason_codes
    if failed:
        return "warn", score, reason_codes
    return "pass", score, []


def aggregate_run(cases: list[dict[str, Any]]) -> tuple[dict[str, Any], float, str]:
    if not cases:
        return {}, 0.0, "block"
    count = len(cases)
    latencies = sorted(int(case.get("latency_ms") or 0) for case in cases)
    p95_index = max(0, math.ceil(0.95 * count) - 1)
    primary_reasons = Counter(case["reason_codes"][0] for case in cases if case.get("reason_codes"))
    all_findings = Counter(reason for case in cases for reason in case.get("reason_codes", []))
    total_tokens = sum(int(case.get("total_tokens") or 0) for case in cases)
    total_cost = sum(float(case.get("estimated_cost") or 0) for case in cases)
    pass_count = sum(case.get("verdict") == "pass" for case in cases)
    security_count = sum(bool(set(case.get("reason_codes", [])) & HARD_BLOCKERS) for case in cases)
    evals = [evaluation for case in cases for evaluation in case.get("evaluations", [])]

    def metric_rate(metric: str) -> float:
        matching = [evaluation for evaluation in evals if evaluation.get("metric") == metric]
        if not matching:
            return 0.0
        by_case: dict[str, list[bool]] = {}
        for evaluation in matching:
            by_case.setdefault(str(evaluation.get("case_id")), []).append(
                bool(evaluation.get("passed", False))
            )
        passing_cases = {
            case_id for case_id, outcomes in by_case.items() if outcomes and all(outcomes)
        }
        return len(passing_cases) / count

    def metric_average(metric: str) -> float | None:
        values = [
            float(evaluation["value"])
            for evaluation in evals
            if evaluation.get("metric") == metric and evaluation.get("value") is not None
        ]
        return sum(values) / len(values) if values else None

    quality = metric_rate("task_success")
    tool_selection = metric_rate("tool_selection")
    tool_arguments = metric_rate("tool_arguments")
    security = 1.0 - security_count / count
    efficiency = (metric_rate("latency") + metric_rate("token_usage")) / 2
    rag_enabled = any(evaluation.get("metric") == "groundedness" for evaluation in evals)
    if rag_enabled:
        retrieval_quality = metric_average("retrieval_recall_at_5")
        groundedness = metric_average("groundedness")
        overall_score = round(
            100
            * (
                0.2 * quality
                + 0.25 * (retrieval_quality if retrieval_quality is not None else 1.0)
                + 0.15 * metric_rate("citation_exists")
                + 0.2 * metric_rate("citation_support")
                + 0.2 * (groundedness if groundedness is not None else 1.0)
            ),
            2,
        )
    else:
        overall_score = round(
            100
            * (
                0.4 * quality
                + 0.15 * tool_selection
                + 0.15 * tool_arguments
                + 0.2 * security
                + 0.1 * efficiency
            ),
            2,
        )
    block_count = sum(case.get("verdict") == "block" for case in cases)
    verdict = (
        "block" if block_count or security_count else ("warn" if pass_count < count else "pass")
    )
    metrics = {
        "task_success": round(quality * 100, 2),
        "tool_selection_accuracy": round(tool_selection * 100, 2),
        "tool_argument_accuracy": round(tool_arguments * 100, 2),
        "security_violations": security_count,
        "average_latency_ms": round(sum(latencies) / count, 2),
        "p95_latency_ms": latencies[p95_index],
        "average_estimated_cost": round(total_cost / count, 6),
        "total_tokens": total_tokens,
        "top_failure_reasons": dict(primary_reasons.most_common(5)),
        "all_failure_findings": dict(all_findings.most_common()),
        "quality_score": round(quality * 100, 2),
        "tool_correctness_score": round(((tool_selection + tool_arguments) / 2) * 100, 2),
        "security_score": round(security * 100, 2),
        "efficiency_score": round(efficiency * 100, 2),
    }
    if rag_enabled:
        rag_latencies = sorted(
            float(evaluation["value"])
            for evaluation in evals
            if evaluation.get("metric") == "rag_latency" and evaluation.get("value") is not None
        )
        rag_p95_index = max(0, math.ceil(0.95 * len(rag_latencies)) - 1) if rag_latencies else 0
        for metric in (
            "retrieval_recall_at_1",
            "retrieval_recall_at_3",
            "retrieval_recall_at_5",
            "mrr",
            "ndcg",
            "citation_precision",
            "citation_recall",
            "groundedness",
            "unsupported_claim_rate",
            "embedding_latency",
            "reranking_latency",
        ):
            average = metric_average(metric)
            metrics[metric] = round(average, 4) if average is not None else None
        metrics["rag_average_latency"] = (
            round(sum(rag_latencies) / len(rag_latencies), 2) if rag_latencies else None
        )
        metrics["rag_p95_latency"] = rag_latencies[rag_p95_index] if rag_latencies else None
    return metrics, overall_score, verdict
