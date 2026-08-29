from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from packages.agent_sdk.contracts import AgentToolCall
from services.tool_gateway.registry import DEMO_TOOL_REGISTRY, TOOL_INPUT_MODELS
from services.tool_gateway.sandbox import SupportSandbox


class GatewayRecord(BaseModel):
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    allowed: bool
    reason_code: str | None = None
    explanation: str
    result: Any | None = None
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int = 0
    execution_failed: bool = False


class ToolGateway:
    def __init__(
        self,
        *,
        registry: list[dict[str, Any]] | None = None,
        forbidden_tools: list[str] | None = None,
        max_tool_calls: int = 10,
        sandbox: SupportSandbox | None = None,
        sandbox_mode: bool = True,
        timeout_tools: list[str] | None = None,
        simulated_timeout_ms: int = 30_000,
    ) -> None:
        effective = DEMO_TOOL_REGISTRY if registry is None else registry
        self.registry = {str(tool["name"]): tool for tool in effective}
        self.forbidden_tools = set(forbidden_tools or [])
        self.max_tool_calls = max_tool_calls
        self.sandbox = sandbox or SupportSandbox()
        self.sandbox_mode = sandbox_mode
        self.timeout_tools = set(timeout_tools or [])
        self.simulated_timeout_ms = simulated_timeout_ms

    async def process(self, calls: list[AgentToolCall]) -> list[GatewayRecord]:
        records: list[GatewayRecord] = []
        for index, call in enumerate(calls):
            started = time.perf_counter()
            record = await self._process_one(call, index)
            if record.duration_ms == 0:
                record.duration_ms = round((time.perf_counter() - started) * 1000)
            records.append(record)
        return records

    async def _process_one(self, call: AgentToolCall, index: int) -> GatewayRecord:
        if index >= self.max_tool_calls:
            return self._denied(
                call, "TOOL_CALL_LIMIT_EXCEEDED", "Per-case tool-call limit exceeded"
            )
        tool = self.registry.get(call.name)
        if tool is None:
            return self._denied(
                call, "UNAUTHORIZED_TOOL_ATTEMPT", "Tool is not in the agent's allow list"
            )
        if call.name in self.forbidden_tools:
            return self._denied(call, "TOOL_POLICY_VIOLATION", "Scenario policy forbids this tool")
        model = TOOL_INPUT_MODELS.get(call.name)
        if model is None:
            return self._denied(
                call, "UNAUTHORIZED_TOOL_ATTEMPT", "No sandbox implementation is registered"
            )
        try:
            parsed = model.model_validate(call.arguments)
        except ValidationError as exc:
            return GatewayRecord(
                tool_call_id=call.tool_call_id,
                name=call.name,
                arguments=call.arguments,
                allowed=False,
                reason_code="INVALID_TOOL_ARGUMENT_SCHEMA",
                explanation="Tool arguments did not match the registered schema",
                validation_errors=exc.errors(include_url=False, include_context=False),
            )
        if bool(tool.get("requires_confirmation")) and not bool(call.arguments.get("confirmed")):
            return self._denied(
                call,
                "TOOL_CONFIRMATION_REQUIRED",
                "High-impact tool requires explicit confirmation",
            )
        if tool.get("risk_class") == "R2" and not self.sandbox_mode:
            return self._denied(
                call, "TOOL_POLICY_VIOLATION", "R2 tools may only execute in the sandbox"
            )
        if call.name in self.timeout_tools:
            return GatewayRecord(
                tool_call_id=call.tool_call_id,
                name=call.name,
                arguments=call.arguments,
                allowed=True,
                reason_code="TOOL_TIMEOUT",
                explanation="Sandbox tool execution exceeded its configured timeout",
                duration_ms=self.simulated_timeout_ms,
                execution_failed=True,
            )
        result = await self.sandbox.execute(call.name, parsed)  # type: ignore[arg-type]
        return GatewayRecord(
            tool_call_id=call.tool_call_id,
            name=call.name,
            arguments=call.arguments,
            allowed=True,
            explanation="Tool call passed policy and schema validation",
            result=result,
        )

    @staticmethod
    def _denied(call: AgentToolCall, reason_code: str, explanation: str) -> GatewayRecord:
        return GatewayRecord(
            tool_call_id=call.tool_call_id,
            name=call.name,
            arguments=call.arguments,
            allowed=False,
            reason_code=reason_code,
            explanation=explanation,
        )
