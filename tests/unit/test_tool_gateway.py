import pytest

from packages.agent_sdk.contracts import AgentToolCall
from services.tool_gateway.gateway import ToolGateway


@pytest.mark.asyncio
async def test_refund_without_confirmation_is_denied_without_side_effect() -> None:
    gateway = ToolGateway()
    records = await gateway.process(
        [
            AgentToolCall(
                tool_call_id="call-1",
                name="refund_order",
                arguments={
                    "order_id": "ORD-1001",
                    "amount": 49.99,
                    "reason": "Requested",
                    "confirmed": False,
                },
            )
        ]
    )
    assert records[0].allowed is False
    assert records[0].reason_code == "TOOL_CONFIRMATION_REQUIRED"
    assert gateway.sandbox.refunds == []


@pytest.mark.asyncio
async def test_refund_with_confirmation_executes_only_in_sandbox() -> None:
    gateway = ToolGateway()
    records = await gateway.process(
        [
            AgentToolCall(
                tool_call_id="call-1",
                name="refund_order",
                arguments={
                    "order_id": "ORD-1001",
                    "amount": 49.99,
                    "reason": "Requested",
                    "confirmed": True,
                },
            )
        ]
    )
    assert records[0].allowed is True
    assert records[0].result["refunded"] is True
    assert len(gateway.sandbox.refunds) == 1


@pytest.mark.asyncio
async def test_argument_validation_returns_structured_failure() -> None:
    records = await ToolGateway().process(
        [AgentToolCall(tool_call_id="call-1", name="get_order", arguments={})]
    )
    assert records[0].allowed is False
    assert records[0].reason_code == "INVALID_TOOL_ARGUMENT_SCHEMA"
    assert records[0].validation_errors[0]["type"] == "missing"


@pytest.mark.asyncio
async def test_forbidden_tool_is_recorded_as_policy_violation() -> None:
    records = await ToolGateway(forbidden_tools=["refund_order"]).process(
        [
            AgentToolCall(
                tool_call_id="call-1",
                name="refund_order",
                arguments={
                    "order_id": "ORD-1001",
                    "amount": 1,
                    "reason": "test",
                    "confirmed": True,
                },
            )
        ]
    )
    assert records[0].reason_code == "TOOL_POLICY_VIOLATION"


@pytest.mark.asyncio
async def test_tool_timeout_preserves_requested_tool_and_skips_execution() -> None:
    gateway = ToolGateway(timeout_tools=["search_customer"], simulated_timeout_ms=2500)
    records = await gateway.process(
        [
            AgentToolCall(
                tool_call_id="call-timeout",
                name="search_customer",
                arguments={"email": "ada@example.com"},
            )
        ]
    )
    assert len(records) == 1
    assert records[0].name == "search_customer"
    assert records[0].allowed is True
    assert records[0].execution_failed is True
    assert records[0].reason_code == "TOOL_TIMEOUT"
    assert records[0].duration_ms == 2500
    assert records[0].result is None
