from packages.agent_sdk.contracts import AgentExecutionResult, AgentToolCall, TokenUsage
from services.evaluators.engine import DeterministicEvaluationEngine
from services.evaluators.models import EvaluationContext
from services.tool_gateway.gateway import GatewayRecord


def execution(*calls: AgentToolCall) -> AgentExecutionResult:
    return AgentExecutionResult(
        final_response="Order found",
        tool_calls=list(calls),
        latency_ms=20,
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def test_expected_and_forbidden_tools_produce_machine_reason_codes() -> None:
    context = EvaluationContext(
        scenario={
            "expected_tools": ["get_order"],
            "forbidden_tools": ["refund_order"],
            "metadata": {},
        },
        execution=execution(),
        gateway_records=[
            GatewayRecord(
                tool_call_id="1",
                name="refund_order",
                arguments={},
                allowed=False,
                reason_code="TOOL_POLICY_VIOLATION",
                explanation="forbidden",
            )
        ],
    )
    results = DeterministicEvaluationEngine().evaluate(context)
    reasons = {result.reason_code for result in results if not result.passed}
    assert "EXPECTED_TOOL_NOT_CALLED" in reasons
    assert "FORBIDDEN_TOOL_CALLED" in reasons
    assert "TOOL_POLICY_VIOLATION" in reasons


def test_exact_output_is_deterministic() -> None:
    context = EvaluationContext(
        scenario={
            "expected_tools": [],
            "forbidden_tools": [],
            "expected_output": "Order found",
            "timeout_seconds": 1,
            "metadata": {"output_match": "exact"},
        },
        execution=execution(),
    )
    results = DeterministicEvaluationEngine().evaluate(context)
    task = next(result for result in results if result.metric == "task_success")
    assert task.passed is True


def test_cost_uses_configured_map_not_hardcoded_pricing() -> None:
    context = EvaluationContext(
        scenario={"metadata": {}},
        execution=execution(),
        pricing={"demo:model": {"input_per_million": 1.0, "output_per_million": 2.0}},
        agent_config={"model_provider": "demo", "model_name": "model"},
    )
    cost = next(
        result
        for result in DeterministicEvaluationEngine().evaluate(context)
        if result.metric == "estimated_cost"
    )
    assert cost.value == 0.00002
