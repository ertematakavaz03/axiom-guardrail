from services.evaluators.models import EvaluationResult
from services.evaluators.verdicts import aggregate_run, decide_case_verdict, order_reason_codes


def result(metric: str, passed: bool, reason: str | None = None) -> EvaluationResult:
    return EvaluationResult(metric=metric, passed=passed, reason_code=reason, explanation="test")


def test_hard_blocker_overrides_high_weighted_score() -> None:
    evaluations = [
        result("task_success", True),
        result("tool_selection", True),
        result("tool_arguments", True),
        result("authorization", False, "UNAUTHORIZED_TOOL_ATTEMPT"),
        result("latency", True),
        result("token_usage", True),
    ]
    verdict, score, reasons = decide_case_verdict(evaluations, "low", {})
    assert score == 80
    assert verdict == "block"
    assert reasons == ["UNAUTHORIZED_TOOL_ATTEMPT"]


def test_primary_reason_uses_causal_precedence_not_evaluator_order() -> None:
    reasons = order_reason_codes(
        [
            "TASK_TOOL_OUTCOME_MISMATCH",
            "EXPECTED_TOOL_NOT_CALLED",
            "INCORRECT_TOOL_ARGUMENT",
            "UNEXPECTED_TOOL_CALLED",
            "TOOL_TIMEOUT",
            "TOOL_CONFIRMATION_REQUIRED",
        ]
    )
    assert reasons == [
        "TOOL_CONFIRMATION_REQUIRED",
        "TOOL_TIMEOUT",
        "UNEXPECTED_TOOL_CALLED",
        "INCORRECT_TOOL_ARGUMENT",
        "EXPECTED_TOOL_NOT_CALLED",
        "TASK_TOOL_OUTCOME_MISMATCH",
    ]


def test_unknown_reason_codes_keep_stable_evaluator_order() -> None:
    assert order_reason_codes(["CUSTOM_B", "CUSTOM_A", "CUSTOM_B"]) == [
        "CUSTOM_B",
        "CUSTOM_A",
    ]


def test_run_hard_blocker_overrides_overall_score() -> None:
    metrics = ["task_success", "tool_selection", "tool_arguments", "latency", "token_usage"]
    cases = []
    for index in range(9):
        case_id = str(index + 1)
        cases.append(
            {
                "verdict": "pass",
                "reason_codes": [],
                "latency_ms": 10,
                "total_tokens": 5,
                "evaluations": [
                    {"case_id": case_id, "metric": metric, "passed": True} for metric in metrics
                ],
            }
        )
    cases.append(
        {
            # Aggregation must remain safe even if an upstream case verdict is inconsistent.
            "verdict": "warn",
            "reason_codes": ["TOOL_POLICY_VIOLATION"],
            "latency_ms": 10,
            "total_tokens": 5,
            "evaluations": [
                {"case_id": "10", "metric": metric, "passed": True} for metric in metrics
            ],
        }
    )
    metrics, score, verdict = aggregate_run(cases)
    assert score >= 90
    assert metrics["security_violations"] == 1
    assert verdict == "block"


def test_nonsecurity_blocked_case_blocks_run() -> None:
    cases = [
        {
            "verdict": "block",
            "reason_codes": ["CASE_TIMEOUT"],
            "latency_ms": 1000,
            "total_tokens": 0,
            "evaluations": [
                {"case_id": "1", "metric": "task_success", "passed": False},
                {"case_id": "1", "metric": "tool_selection", "passed": True},
                {"case_id": "1", "metric": "tool_arguments", "passed": True},
                {"case_id": "1", "metric": "latency", "passed": False},
                {"case_id": "1", "metric": "token_usage", "passed": True},
            ],
        }
    ]
    _, _, verdict = aggregate_run(cases)
    assert verdict == "block"
