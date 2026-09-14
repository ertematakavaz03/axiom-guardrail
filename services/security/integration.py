from __future__ import annotations

import asyncio
from typing import Any

from apps.api.app.config import Settings
from packages.agent_sdk.contracts import AgentExecutionRequest
from services.evaluators.models import EvaluationResult
from services.orchestrator.adapters import adapter_for
from services.orchestrator.graph import OrchestrationResult, TraceEvent
from services.security.mcp import MCPInventory
from services.security.models import Principal, SecurityPolicy, SecurityScenario
from services.security.runner import observe_external, run_demo_case


async def run_security_case(
    *,
    settings: Settings,
    run_id: str,
    case_id: str,
    scenario: dict[str, Any],
    agent_config: dict[str, Any],
    security_config: dict[str, Any],
) -> OrchestrationResult:
    security = SecurityScenario.model_validate(scenario["metadata"]["security"])
    policy = SecurityPolicy.model_validate(security_config["policy"])
    application_principal = {**security_config["principal"], "run_id": run_id}
    principal = Principal.model_validate(application_principal)
    if agent_config["adapter_type"] == "demo_security_agent":
        # The demo's protected resources are synthetic, separate from the authenticated
        # Axiom account that owns the run. Never pretend these fixture IDs are real users.
        principal = Principal(
            tenant_id="tenant-a",
            project_id="project-a",
            user_id="user-a",
            run_id=run_id,
            permissions={"refund"},
        )
        result = await run_demo_case(
            security,
            policy,
            principal,
            security_config["mode"],
            approved_inventories=[
                MCPInventory.model_validate(item)
                for item in security_config.get("mcp_approved", [])
            ],
        )
    else:
        request = AgentExecutionRequest(
            run_id=run_id,
            case_id=case_id,
            scenario=scenario,
            agent_config=agent_config,
            messages=security.messages or [{"role": "user", "content": security.input}],
            sandbox_context={"security_context": security.setup},
        )
        async with asyncio.timeout(int(scenario.get("timeout_seconds", 30))):
            execution = await adapter_for(agent_config, settings).execute(request)
        result = observe_external(security, execution, policy, principal)
    evaluation = result.evaluation
    traces = [
        TraceEvent(
            sequence_number=index + 1,
            event_type=f"security_{event.kind}",
            name=event.id,
            payload=event.model_dump(mode="json"),
        )
        for index, event in enumerate(result.events)
    ]
    traces.append(
        TraceEvent(
            sequence_number=len(traces) + 1,
            event_type="security_context",
            name="application_scope",
            payload={
                "application_principal": application_principal,
                "target_principal": principal.model_dump(mode="json"),
                "policy_hash": security_config["policy_hash"],
            },
        )
    )
    summary = {
        "evaluation": evaluation.model_dump(mode="json"),
        "gateway_overhead_ms": result.gateway_overhead_ms,
        "mcp_overhead_ms": result.mcp_overhead_ms,
        "policy_overhead_ms": result.policy_overhead_ms,
        "evaluator_overhead_ms": result.evaluator_overhead_ms,
        "synthetic_sandbox_effects": result.sandbox_effects,
        "scenario": security.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
    }
    evaluations = [
        EvaluationResult(
            metric="security_summary",
            passed=evaluation.verdict == "pass",
            explanation="Deterministic trace-backed security evaluation; verdict is distinct from attack outcome",
            actual=evaluation.outcome.value,
            evidence=summary,
        )
    ]
    for finding in evaluation.findings:
        evaluations.append(
            EvaluationResult(
                metric="security_finding",
                passed=False,
                reason_code=finding.reason_code,
                explanation=finding.description,
                evidence=finding.model_dump(mode="json"),
            )
        )
    # Legacy numeric score is a verdict projection, not a claim of security strength.
    return OrchestrationResult(
        final_response=result.final_response,
        traces=traces,
        evaluations=evaluations,
        verdict=evaluation.verdict,
        score=100 if evaluation.verdict == "pass" else 0,
        reason_codes=list(dict.fromkeys(f.reason_code for f in evaluation.findings)),
        latency_ms=round(result.latency_ms),
        estimated_cost=None,
    )
