from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from apps.api.app.config import Settings
from demos.support_agent.seed import SCENARIOS as SEEDED_SCENARIOS
from services.evaluators.verdicts import aggregate_run
from services.orchestrator.graph import CaseOrchestrator, OrchestrationResult
from services.tool_gateway.registry import DEMO_TOOL_REGISTRY

SCENARIOS: list[dict[str, Any]] = json.loads(
    Path("tests/golden/support_agent.json").read_text(encoding="utf-8")
)


def test_seeded_suite_matches_the_audited_golden_contract() -> None:
    assert [scenario["name"] for scenario in SEEDED_SCENARIOS] == [
        scenario["name"] for scenario in SCENARIOS
    ]
    semantic_fields = (
        "input",
        "expected_tools",
        "forbidden_tools",
        "expected_tool_arguments",
        "severity",
        "metadata",
    )
    for seeded, golden in zip(SEEDED_SCENARIOS, SCENARIOS, strict=True):
        for field in semantic_fields:
            default: Any = [] if field in {"expected_tools", "forbidden_tools"} else None
            if field == "metadata":
                default = {}
            assert seeded.get(field, default) == golden.get(field, default), (
                seeded["name"],
                field,
            )


async def run_scenario(scenario: dict[str, Any]) -> OrchestrationResult:
    return await CaseOrchestrator(Settings()).run_case(
        run_id=str(uuid.uuid4()),
        case_id=str(uuid.uuid4()),
        scenario={**scenario, "expected_output": None, "timeout_seconds": 2},
        agent_config={
            "adapter_type": "demo_support_agent",
            "model_provider": "demo",
            "model_name": "deterministic-support-v1",
            "tool_registry": DEMO_TOOL_REGISTRY,
        },
        suite_config={"gate_policy": {"block_severities": ["critical"]}},
        budget={
            "per_case_timeout_seconds": 2,
            "max_tool_calls_per_case": 10,
            "max_total_tokens": 1000,
        },
    )


def metric_outcomes(result: OrchestrationResult) -> dict[str, bool]:
    metrics: dict[str, list[bool]] = {}
    for evaluation in result.evaluations:
        metrics.setdefault(evaluation.metric, []).append(evaluation.passed)
    return {metric: all(outcomes) for metric, outcomes in metrics.items()}


def requested_tools(result: OrchestrationResult) -> list[str]:
    return [
        str(trace.name)
        for trace in result.traces
        if trace.event_type == "tool_requested" and trace.name is not None
    ]


@pytest.mark.asyncio
async def test_all_twelve_golden_scenarios_are_semantically_aligned() -> None:
    assert len(SCENARIOS) == 12
    case_summaries: list[dict[str, Any]] = []

    for index, scenario in enumerate(SCENARIOS, start=1):
        expected_tools = set(scenario["expected_tools"])
        forbidden_tools = set(scenario["forbidden_tools"])
        expected_arguments = scenario.get("expected_tool_arguments") or {}
        assert expected_tools.isdisjoint(forbidden_tools), scenario["name"]
        assert set(expected_arguments) <= (expected_tools | forbidden_tools), scenario["name"]

        result = await run_scenario(scenario)
        expected = scenario["expected"]
        expected_primary = expected["primary_reason_code"]

        assert result.verdict == expected["verdict"], scenario["name"]
        assert (result.reason_codes[0] if result.reason_codes else None) == expected_primary, (
            scenario["name"]
        )
        assert requested_tools(result) == expected["actual_tools"], scenario["name"]
        actual_metric_pass = metric_outcomes(result)
        for metric, expected_pass in expected["metric_pass"].items():
            assert actual_metric_pass[metric] is expected_pass, scenario["name"]
        for reason_code in expected.get("reason_codes_contain", []):
            assert reason_code in result.reason_codes, scenario["name"]

        case_id = str(index)
        case_summaries.append(
            {
                "verdict": result.verdict,
                "reason_codes": result.reason_codes,
                "latency_ms": result.latency_ms,
                "total_tokens": result.total_tokens,
                "estimated_cost": result.estimated_cost,
                "evaluations": [
                    {
                        "case_id": case_id,
                        "metric": evaluation.metric,
                        "passed": evaluation.passed,
                    }
                    for evaluation in result.evaluations
                ],
            }
        )

    metrics, overall_score, verdict = aggregate_run(case_summaries)
    primary_counts = Counter(
        summary["reason_codes"][0] for summary in case_summaries if summary["reason_codes"]
    )

    assert Counter(summary["verdict"] for summary in case_summaries) == {
        "pass": 4,
        "warn": 5,
        "block": 3,
    }
    assert metrics["task_success"] == 33.33
    assert metrics["tool_selection_accuracy"] == 75.0
    assert metrics["tool_argument_accuracy"] == 83.33
    assert metrics["security_violations"] == 2
    assert metrics["quality_score"] == 33.33
    assert metrics["tool_correctness_score"] == 79.17
    assert metrics["security_score"] == 83.33
    assert metrics["efficiency_score"] == 95.83
    assert metrics["all_failure_findings"]["TOOL_TIMEOUT"] >= 1
    assert sum(metrics["top_failure_reasons"].values()) <= sum(primary_counts.values())
    assert overall_score == 63.33
    assert verdict == "block"


@pytest.mark.parametrize(
    ("scenario_name", "primary_reason"),
    [
        ("Refund requires confirmation", "TOOL_CONFIRMATION_REQUIRED"),
        ("Tool timeout", "TOOL_TIMEOUT"),
        ("Unexpected tool call", "UNEXPECTED_TOOL_CALLED"),
    ],
)
@pytest.mark.asyncio
async def test_corrected_primary_reason_regressions(
    scenario_name: str, primary_reason: str
) -> None:
    scenario = next(item for item in SCENARIOS if item["name"] == scenario_name)
    result = await run_scenario(scenario)
    assert result.reason_codes[0] == primary_reason
