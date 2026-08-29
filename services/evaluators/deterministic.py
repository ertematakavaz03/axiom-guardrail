from __future__ import annotations

from typing import Any

from services.evaluators.models import EvaluationContext, EvaluationResult


def _result(
    metric: str,
    passed: bool,
    explanation: str,
    *,
    reason_code: str | None = None,
    expected: Any = None,
    actual: Any = None,
    evidence: dict[str, Any] | None = None,
    value: float | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        metric=metric,
        passed=passed,
        reason_code=reason_code,
        explanation=explanation,
        expected=expected,
        actual=actual,
        evidence=evidence or {},
        value=value if value is not None else (1.0 if passed else 0.0),
    )


class ToolSelectionEvaluator:
    name = "tool_selection"
    version = "1.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        expected = set(context.scenario.get("expected_tools", []))
        forbidden = set(context.scenario.get("forbidden_tools", []))
        actual = [record.name for record in context.gateway_records]
        actual_set = set(actual)
        results: list[EvaluationResult] = []
        for name in sorted(expected - actual_set):
            results.append(
                _result(
                    self.name,
                    False,
                    f"Expected tool '{name}' was not called",
                    reason_code="EXPECTED_TOOL_NOT_CALLED",
                    expected=sorted(expected),
                    actual=actual,
                )
            )
        for name in sorted(forbidden & actual_set):
            results.append(
                _result(
                    self.name,
                    False,
                    f"Forbidden tool '{name}' was called",
                    reason_code="FORBIDDEN_TOOL_CALLED",
                    expected={"forbidden": sorted(forbidden)},
                    actual=actual,
                    evidence={"tool": name},
                )
            )
        allowed_expected = expected | forbidden
        if expected and context.scenario.get("metadata", {}).get("reject_unexpected_tools", True):
            for name in sorted(actual_set - allowed_expected):
                results.append(
                    _result(
                        self.name,
                        False,
                        f"Unexpected tool '{name}' was called",
                        reason_code="UNEXPECTED_TOOL_CALLED",
                        expected=sorted(expected),
                        actual=actual,
                        evidence={"tool": name},
                    )
                )
        if not results:
            results.append(
                _result(
                    self.name,
                    True,
                    "Observed tool selection matched scenario policy",
                    expected={"required": sorted(expected), "forbidden": sorted(forbidden)},
                    actual=actual,
                )
            )
        return results


class ToolArgumentsEvaluator:
    name = "tool_arguments"
    version = "1.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        expected: dict[str, dict[str, Any]] = context.scenario.get("expected_tool_arguments") or {}
        results: list[EvaluationResult] = []
        for record in context.gateway_records:
            if record.reason_code == "INVALID_TOOL_ARGUMENT_SCHEMA":
                missing = any(error.get("type") == "missing" for error in record.validation_errors)
                results.append(
                    _result(
                        self.name,
                        False,
                        f"Arguments for '{record.name}' failed registered schema validation",
                        reason_code=(
                            "MISSING_REQUIRED_TOOL_ARGUMENT"
                            if missing
                            else "INVALID_TOOL_ARGUMENT_SCHEMA"
                        ),
                        expected={"valid_schema": True},
                        actual=record.arguments,
                        evidence={"validation_errors": record.validation_errors},
                    )
                )
            for key, expected_value in expected.get(record.name, {}).items():
                if key not in record.arguments:
                    results.append(
                        _result(
                            self.name,
                            False,
                            f"Required expected argument '{key}' was missing for '{record.name}'",
                            reason_code="MISSING_REQUIRED_TOOL_ARGUMENT",
                            expected={key: expected_value},
                            actual=record.arguments,
                            evidence={"tool_call_id": record.tool_call_id},
                        )
                    )
                elif record.arguments[key] != expected_value:
                    results.append(
                        _result(
                            self.name,
                            False,
                            f"Argument '{key}' for '{record.name}' had an incorrect value",
                            reason_code="INCORRECT_TOOL_ARGUMENT",
                            expected={key: expected_value},
                            actual={key: record.arguments[key]},
                            evidence={"tool_call_id": record.tool_call_id},
                        )
                    )
        if not results:
            results.append(
                _result(
                    self.name, True, "All observed tool arguments were valid", expected=expected
                )
            )
        return results


class AuthorizationEvaluator:
    name = "authorization"
    version = "1.0"
    authorization_codes = {
        "UNAUTHORIZED_TOOL_ATTEMPT",
        "TOOL_CONFIRMATION_REQUIRED",
        "TOOL_POLICY_VIOLATION",
        "TOOL_CALL_LIMIT_EXCEEDED",
    }

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        denied = [
            record
            for record in context.gateway_records
            if record.reason_code in self.authorization_codes
        ]
        if not denied:
            return [_result(self.name, True, "No authorization or tool-policy violation occurred")]
        return [
            _result(
                self.name,
                False,
                record.explanation,
                reason_code=record.reason_code,
                expected={"policy_decision": "allow only when all requirements pass"},
                actual={"policy_decision": "deny", "arguments": record.arguments},
                evidence={"tool_call_id": record.tool_call_id, "tool": record.name},
            )
            for record in denied
        ]


class TaskSuccessEvaluator:
    name = "task_success"
    version = "1.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        if context.execution is None:
            return [
                _result(
                    self.name,
                    False,
                    "Agent execution did not produce a valid response",
                    reason_code=context.errors[-1]["reason_code"]
                    if context.errors
                    else "AGENT_EXECUTION_FAILED",
                    expected="valid agent response",
                    actual=context.errors,
                )
            ]
        failed_tools = [
            record
            for record in context.gateway_records
            if not record.allowed or record.execution_failed
        ]
        if failed_tools:
            failure = failed_tools[0]
            return [
                _result(
                    self.name,
                    False,
                    f"Required tool execution failed: {failure.explanation}",
                    reason_code=failure.reason_code or "TASK_TOOL_OUTCOME_MISMATCH",
                    expected={"tool_execution": "completed"},
                    actual={
                        "tool": failure.name,
                        "tool_execution": "failed",
                        "reason_code": failure.reason_code,
                    },
                    evidence={"tool_call_id": failure.tool_call_id},
                )
            ]
        expected_tools = set(context.scenario.get("expected_tools", []))
        forbidden_tools = set(context.scenario.get("forbidden_tools", []))
        actual_tools = {record.name for record in context.gateway_records}
        unexpected_tools = actual_tools - expected_tools - forbidden_tools
        if unexpected_tools and context.scenario.get("metadata", {}).get(
            "reject_unexpected_tools", True
        ):
            return [
                _result(
                    self.name,
                    False,
                    "Task behavior included an unexpected tool call",
                    reason_code="UNEXPECTED_TOOL_CALLED",
                    expected=sorted(expected_tools),
                    actual=sorted(actual_tools),
                    evidence={"unexpected_tools": sorted(unexpected_tools)},
                )
            ]
        missing_tools = expected_tools - actual_tools
        if missing_tools:
            return [
                _result(
                    self.name,
                    False,
                    "Task could not succeed because a required tool was not called",
                    reason_code="EXPECTED_TOOL_NOT_CALLED",
                    expected=sorted(expected_tools),
                    actual=sorted(actual_tools),
                    evidence={"missing_tools": sorted(missing_tools)},
                )
            ]
        expected_output = context.scenario.get("expected_output")
        metadata = context.scenario.get("metadata", {})
        if expected_output is not None:
            mode = metadata.get("output_match", "contains")
            actual = context.execution.final_response
            passed = (
                actual == expected_output
                if mode == "exact"
                else expected_output.lower() in actual.lower()
            )
            return [
                _result(
                    self.name,
                    passed,
                    f"Final response {'matched' if passed else 'did not match'} using {mode} mode",
                    reason_code=None if passed else "TASK_OUTPUT_MISMATCH",
                    expected={"mode": mode, "text": expected_output},
                    actual=actual,
                )
            ]
        outcome = metadata.get("expected_tool_outcome")
        if outcome:
            matching = [
                record for record in context.gateway_records if record.name == outcome.get("tool")
            ]
            value = (
                _nested_value(matching[-1].result, outcome.get("field", "")) if matching else None
            )
            passed = value == outcome.get("equals")
            return [
                _result(
                    self.name,
                    passed,
                    "Tool outcome matched expectation"
                    if passed
                    else "Tool outcome did not match expectation",
                    reason_code=None if passed else "TASK_TOOL_OUTCOME_MISMATCH",
                    expected=outcome,
                    actual=value,
                    evidence={"tool": outcome.get("tool")},
                )
            ]
        return [_result(self.name, True, "No deterministic output assertion was configured")]


class LatencyEvaluator:
    name = "latency"
    version = "1.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        latency = (context.execution.latency_ms if context.execution else 0) + sum(
            record.duration_ms for record in context.gateway_records
        )
        limit_ms = int(context.scenario.get("timeout_seconds", 30)) * 1000
        tool_timeout = next(
            (record for record in context.gateway_records if record.reason_code == "TOOL_TIMEOUT"),
            None,
        )
        passed = context.execution is not None and latency <= limit_ms and tool_timeout is None
        return [
            _result(
                self.name,
                passed,
                "Case latency was within its timeout"
                if passed
                else "Case exceeded or failed its timeout",
                reason_code=None if passed else ("TOOL_TIMEOUT" if tool_timeout else "CASE_TIMEOUT"),
                expected={"max_ms": limit_ms},
                actual={"latency_ms": latency},
                value=float(latency),
            )
        ]


class TokenUsageEvaluator:
    name = "token_usage"
    version = "1.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        total = context.execution.token_usage.total_tokens if context.execution else None
        max_tokens = context.budget.get("max_total_tokens")
        passed = total is None or max_tokens is None or total <= max_tokens
        return [
            _result(
                self.name,
                passed,
                "Token usage recorded within configured budget"
                if passed
                else "Token budget exceeded",
                reason_code=None if passed else "TOKEN_BUDGET_EXCEEDED",
                expected={"max_total_tokens": max_tokens},
                actual={"total_tokens": total},
                value=float(total or 0),
            )
        ]


class CostEvaluator:
    name = "estimated_cost"
    version = "1.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        provider = context.agent_config.get("model_provider", "")
        model = context.agent_config.get("model_name", "")
        pricing = context.pricing.get(f"{provider}:{model}") or context.pricing.get(model)
        usage = context.execution.token_usage if context.execution else None
        if not pricing or usage is None:
            cost = 0.0
            explanation = "No pricing entry configured; estimated cost recorded as zero"
        else:
            cost = (usage.input_tokens or 0) / 1_000_000 * pricing.get("input_per_million", 0.0) + (
                usage.output_tokens or 0
            ) / 1_000_000 * pricing.get("output_per_million", 0.0)
            explanation = "Estimated cost calculated from the configured pricing map"
        return [
            _result(
                self.name,
                True,
                explanation,
                actual={"currency": "USD", "estimated_cost": cost},
                evidence={"pricing_key": f"{provider}:{model}"},
                value=cost,
            )
        ]


def _nested_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split(".") if path else []:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
