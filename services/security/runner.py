from __future__ import annotations

import time
from contextlib import AsyncExitStack
from typing import Any, Literal

from demos.security_lab.target import VulnerableAgent, VulnerableSandbox, encode, resolve
from packages.agent_sdk.contracts import AgentExecutionResult
from services.security.evaluator import evaluate_security
from services.security.gateway import SecurityGateway
from services.security.mcp import LocalMCPClient, MCPInventory, MCPRegistry
from services.security.models import (
    Action,
    Principal,
    SecurityEvaluation,
    SecurityEvent,
    SecurityPolicy,
    SecurityScenario,
    StrictModel,
)
from services.security.policy import ConfirmationStore, PolicyEngine


class SecurityCaseExecution(StrictModel):
    evaluation: SecurityEvaluation
    events: list[SecurityEvent]
    final_response: str
    latency_ms: float
    gateway_overhead_ms: list[float]
    mcp_overhead_ms: list[float]
    sandbox_effects: list[dict[str, Any]]
    policy_overhead_ms: list[float]
    evaluator_overhead_ms: float


async def run_demo_case(
    scenario: SecurityScenario,
    policy: SecurityPolicy,
    principal: Principal,
    mode: Literal["observational", "preventive"] = "preventive",
    approved_inventories: list[MCPInventory] | None = None,
) -> SecurityCaseExecution:
    started = time.perf_counter()
    engine = PolicyEngine(policy)
    sandbox = VulnerableSandbox()
    events = [
        SecurityEvent(
            id="input",
            kind="input",
            source="harness",
            payload={
                "scenario": scenario.model_dump(mode="json"),
                "principal": principal.model_dump(mode="json"),
            },
        )
    ]
    commands = VulnerableAgent().commands(scenario, events)
    store = ConfirmationStore()
    mcp_overhead: list[float] = []
    async with AsyncExitStack() as stack:
        clients: dict[str, LocalMCPClient] = {}
        approved: list[MCPInventory] = []
        if scenario.setup.get("mcp"):
            for profile in ("benign", "adversarial"):
                client = await stack.enter_async_context(LocalMCPClient(profile))
                baseline = await client.inventory()
                approved.append(baseline)
                clients[baseline.server] = client
            if scenario.setup.get("drift"):
                clients["local-benign"] = await stack.enter_async_context(
                    LocalMCPClient("benign", 2)
                )
        registry = MCPRegistry(approved if approved_inventories is None else approved_inventories)

        async def executor(action: Action, context: Principal) -> Any:
            if not action.server:
                return await sandbox.execute(action, context)
            # Unknown server requests are simulated locally; never contact an arbitrary endpoint.
            if action.server not in clients:
                return {"synthetic_unapproved_server_effect": True}
            params: dict[str, Any] = {"_meta": {"axiom/scope": context.model_dump(mode="json")}}
            if action.operation == "resources/read":
                params["uri"] = action.tool
            else:
                params.update({"name": action.tool, "arguments": action.arguments})
            return await clients[action.server].request(action.operation, params)

        gateway = SecurityGateway(
            engine, principal, executor, confirmations=store, timeout_seconds=1
        )
        last: Any = None
        final = "No output produced"
        grant: str | None = None
        for index, step in enumerate(commands):
            operation = step["op"]
            if operation == "call":
                action = Action(
                    id=f"action-{index}",
                    tool=step["tool"],
                    arguments=resolve(step.get("arguments", {}), last),
                    server=step.get("server"),
                    operation=step.get("operation", "tools/call"),
                    confirmation_id=step.get("confirmation_id"),
                )
                if (
                    scenario.setup.get("confirmation") == "replay"
                    and action.tool == "release_report"
                ):
                    if grant is None:
                        grant = store.issue(principal, action)
                    action.confirmation_id = grant
                guard = None
                if action.server and action.server in clients:
                    observed = await clients[action.server].inventory()
                    guard_start = time.perf_counter_ns()
                    guard = registry.guard(observed)
                    mcp_overhead.append((time.perf_counter_ns() - guard_start) / 1_000_000)
                    events.append(
                        SecurityEvent(
                            id=f"inventory-{index}",
                            kind="mcp_inventory",
                            source="mcp",
                            payload={
                                "observed": observed.model_dump(),
                                "approved": registry.approved[action.server].model_dump()
                                if action.server in registry.approved
                                else None,
                                "peers": [item.model_dump() for item in approved],
                            },
                        )
                    )
                if mode == "preventive":
                    before = len(gateway.events)
                    last = await gateway.execute(action, guard=guard)
                    events.extend(gateway.events[before:])
                else:
                    events.append(
                        SecurityEvent(
                            id=f"request-{index}",
                            kind="action_requested",
                            source="adapter",
                            payload={"action": action.model_dump()},
                        )
                    )
                    try:
                        last = await executor(action, principal)
                        events.append(
                            SecurityEvent(
                                id=f"completed-{index}",
                                kind="action_completed",
                                source="adapter",
                                payload={"action_id": action.id, "result": last},
                            )
                        )
                    except Exception as exc:
                        last = {"error": type(exc).__name__}
                        events.append(
                            SecurityEvent(
                                id=f"failed-{index}",
                                kind="action_failed",
                                source="adapter",
                                payload={
                                    "action_id": action.id,
                                    "exception_type": type(exc).__name__,
                                },
                            )
                        )
            elif operation == "encode":
                last = encode(last, step["encoding"])
            elif operation == "log":
                events.append(
                    SecurityEvent(
                        id=f"log-{index}",
                        kind="log",
                        source="agent",
                        payload={"text": resolve(step["value"], last)},
                    )
                )
            elif operation == "respond":
                final = encode(resolve(step["value"], last), step.get("encoding", "literal"))
                events.append(
                    SecurityEvent(
                        id=f"response-{index}",
                        kind="response",
                        source="agent",
                        payload={"text": final},
                    )
                )
        for name, client in clients.items():
            events.append(
                SecurityEvent(
                    id=f"protocol-{name}",
                    kind="context",
                    source="mcp",
                    payload={"server": name, "protocol_transcript": client.transcript},
                )
            )
        evaluator_started = time.perf_counter_ns()
        evaluation = evaluate_security(
            scenario, events, engine, principal, mode=mode, receipts=gateway.receipts
        )
        evaluator_ms = (time.perf_counter_ns() - evaluator_started) / 1_000_000
    return SecurityCaseExecution(
        evaluation=evaluation,
        events=events,
        final_response=final,
        latency_ms=(time.perf_counter() - started) * 1000,
        gateway_overhead_ms=gateway.overhead_ms,
        mcp_overhead_ms=mcp_overhead,
        sandbox_effects=sandbox.effects,
        policy_overhead_ms=engine.overhead_ms,
        evaluator_overhead_ms=evaluator_ms,
    )


def observe_external(
    scenario: SecurityScenario,
    execution: AgentExecutionResult,
    policy: SecurityPolicy,
    principal: Principal,
) -> SecurityCaseExecution:
    """Framework-neutral legacy adapter import. Missing tool results mean review.

    raw_metadata cannot provide trusted enforcement receipts or override mode.
    """
    events = [
        SecurityEvent(
            id="input",
            kind="input",
            source="harness",
            payload={"scenario": scenario.model_dump(mode="json")},
        )
    ]
    for index, call in enumerate(execution.tool_calls):
        action = Action(id=call.tool_call_id, tool=call.name, arguments=call.arguments)
        events.append(
            SecurityEvent(
                id=f"request-{index}",
                kind="action_requested",
                source="adapter",
                payload={"action": action.model_dump()},
            )
        )
        if call.result is not None:
            events.append(
                SecurityEvent(
                    id=f"completed-{index}",
                    kind="action_completed",
                    source="adapter",
                    payload={"action_id": action.id, "result": call.result},
                )
            )
    events.append(
        SecurityEvent(
            id="response",
            kind="response",
            source="adapter",
            payload={
                "text": execution.final_response,
                "raw_execution": execution.model_dump(mode="json"),
            },
        )
    )
    engine = PolicyEngine(policy)
    evaluator_started = time.perf_counter_ns()
    evaluation = evaluate_security(scenario, events, engine, principal)
    evaluator_ms = (time.perf_counter_ns() - evaluator_started) / 1_000_000
    return SecurityCaseExecution(
        evaluation=evaluation,
        events=events,
        final_response=execution.final_response,
        latency_ms=execution.latency_ms,
        gateway_overhead_ms=[],
        mcp_overhead_ms=[],
        sandbox_effects=[],
        policy_overhead_ms=engine.overhead_ms,
        evaluator_overhead_ms=evaluator_ms,
    )
