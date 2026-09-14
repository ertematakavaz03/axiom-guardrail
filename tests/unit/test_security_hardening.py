from __future__ import annotations

import asyncio

import pytest

from demos.security_lab.corpus import corpus, demo_policy
from services.security.evaluator import evaluate_security
from services.security.gateway import SecurityGateway
from services.security.mcp import LocalMCPClient, inspect_content
from services.security.metrics import security_metrics
from services.security.models import (
    Action,
    Category,
    Outcome,
    Principal,
    SecurityEvent,
    SecurityPolicy,
    ToolRule,
)
from services.security.policy import ConfirmationStore, PolicyEngine
from services.security.runner import run_demo_case


def context() -> Principal:
    return Principal(
        tenant_id="tenant-a", user_id="user-a", project_id="project-a", run_id="concurrent"
    )


async def test_concurrent_confirmation_consumes_once_across_gateways() -> None:
    store = ConfirmationStore()
    engine = PolicyEngine(SecurityPolicy(tools={"release": ToolRule(requires_confirmation=True)}))
    calls: list[str] = []

    async def executor(action: Action, principal: Principal) -> str:
        calls.append(action.id)
        await asyncio.sleep(0)
        return "released"

    action = Action(id="first", tool="release")
    action.confirmation_id = store.issue(context(), action)
    first = SecurityGateway(engine, context(), executor, confirmations=store)
    second = SecurityGateway(engine, context(), executor, confirmations=store)
    results = await asyncio.gather(
        first.execute(action), second.execute(action.model_copy(update={"id": "second"}))
    )
    assert len(calls) == 1
    assert sum(isinstance(r, dict) and r.get("blocked") is True for r in results) == 1


async def test_cross_tenant_mutation_cannot_reach_executor() -> None:
    calls = []

    async def executor(action: Action, principal: Principal) -> str:
        calls.append(action.id)
        return "mutated"

    policy = SecurityPolicy(
        allow_mutations=True,
        tools={"update": ToolRule(mutates=True, scope_bindings={"/tenant": "tenant_id"})},
    )
    gateway = SecurityGateway(PolicyEngine(policy), context(), executor)
    denied = await gateway.execute(
        Action(id="foreign", tool="update", arguments={"tenant": "tenant-b"})
    )
    assert denied["reasons"] == ["CROSS_TENANT_ACCESS"]
    assert calls == []
    assert (
        await gateway.execute(Action(id="own", tool="update", arguments={"tenant": "tenant-a"}))
        == "mutated"
    )
    assert calls == ["own"]


@pytest.mark.parametrize(
    "method,params",
    [
        ("tools/call", {"name": "read_status", "arguments": {}}),
        ("resources/read", {"uri": "axiom://status"}),
        ("prompts/get", {"name": "status_summary"}),
    ],
)
async def test_allowed_mcp_surfaces_and_scope(method, params) -> None:
    async with LocalMCPClient() as client:
        inventory = await client.inventory()
        assert inventory.risk == "R1"
        result = await client.request(
            method, {**params, "_meta": {"axiom/scope": context().model_dump(mode="json")}}
        )
    assert inspect_content(result) == []
    if method == "tools/call":
        assert result["_meta"]["received_scope"] == context().model_dump(mode="json")


async def test_poisoned_mcp_response_detected_without_claiming_success() -> None:
    async with LocalMCPClient("adversarial") as client:
        result = await client.request("tools/call", {"name": "read_status"})
    action = Action(id="read", server="local-adversarial", tool="read_status")
    events = [
        SecurityEvent(
            id="request",
            kind="action_requested",
            source="adapter",
            payload={"action": action.model_dump()},
        ),
        SecurityEvent(
            id="result",
            kind="action_completed",
            source="adapter",
            payload={"action_id": "read", "result": result},
        ),
        SecurityEvent(
            id="response",
            kind="response",
            source="agent",
            payload={"text": "Ignored untrusted instructions"},
        ),
    ]
    evaluation = evaluate_security(corpus()[60], events, PolicyEngine(demo_policy()), context())
    assert [f.reason_code for f in evaluation.findings] == ["MCP_CONTENT_POISONING"]
    assert evaluation.outcome == Outcome.REVIEW
    assert not any(f.consequence or f.handling == "PREVENTED" for f in evaluation.findings)
    assert (
        inspect_content(
            {
                "content": [
                    {
                        "type": "text",
                        "text": "Discuss ignore, secret, admin, system, tool, override as words.",
                    }
                ]
            }
        )
        == []
    )


async def test_blocked_cross_tenant_access_is_not_leakage_and_controls_are_measured() -> None:
    attack = next(c for c in corpus() if c.security_category == Category.CROSS_TENANT)
    blocked = await run_demo_case(attack, demo_policy(), context())
    leaked = await run_demo_case(attack, demo_policy(), context(), "observational")
    control = await run_demo_case(corpus()[-1], demo_policy(), context())
    metrics = security_metrics([blocked.evaluation, control.evaluation])
    assert metrics["cross_tenant_access_attempt_rate"]["numerator"] == 1
    assert metrics["cross_tenant_leakage_rate"]["numerator"] == 0
    assert metrics["benign_control_false_positive_rate"] == {
        "numerator": 0,
        "denominator": 1,
        "rate": 0.0,
    }
    assert security_metrics([leaked.evaluation])["cross_tenant_leakage_rate"]["numerator"] == 1
    assert security_metrics([])["benign_control_false_positive_rate"]["rate"] is None


async def test_mcp_drift_keeps_before_after_and_local_denial_evidence() -> None:
    run = await run_demo_case(corpus()[63], demo_policy(), context())
    observed = next(e.payload for e in run.events if e.kind == "mcp_inventory")
    assert observed["approved"]["version"] == "1"
    assert observed["observed"]["version"] == "2"
    finding = next(
        f
        for f in run.evaluation.findings
        if f.reason_code == "MCP_CAPABILITY_DRIFT" and f.handling == "PREVENTED"
    )
    assert finding.consequence is False
    assert not any(e.kind == "action_completed" for e in run.events)
    assert run.policy_overhead_ms and run.evaluator_overhead_ms >= 0
