from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from apps.api.app.db.models import (
    Agent,
    AgentVersion,
    CaseResult,
    CaseStatus,
    EvalResult,
    OrganizationMember,
    Project,
    Run,
    RunStatus,
    Scenario,
    Severity,
    TestSuite,
    Trace,
    User,
    Verdict,
)
from apps.api.app.db.session import SessionLocal

ROOT = Path(__file__).resolve().parent
PROJECT_NAME = "External Benchmarks"
AGENT_NAME = "LangGraph Customer Support Agent"
SUITE_NAME = "External LangGraph Support Benchmark v1"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def numeric_usage(execution: dict[str, Any]) -> dict[str, int | None]:
    usage = execution.get("usage")
    if isinstance(usage, dict) and all(
        isinstance(usage.get(key), int) for key in ("input_tokens", "output_tokens", "total_tokens")
    ):
        return {key: int(usage[key]) for key in usage}
    input_tokens = 0
    output_tokens = 0
    available = False
    for message in execution.get("messages", []):
        metadata = message.get("model_metadata")
        if not isinstance(metadata, dict):
            continue
        if isinstance(metadata.get("prompt_eval_count"), int):
            input_tokens += metadata["prompt_eval_count"]
            available = True
        if isinstance(metadata.get("eval_count"), int):
            output_tokens += metadata["eval_count"]
            available = True
    return {
        "input_tokens": input_tokens if available else None,
        "output_tokens": output_tokens if available else None,
        "total_tokens": input_tokens + output_tokens if available else None,
    }


def product_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    quality = summary["quality"]
    performance = summary["performance"]
    total_latency = performance["total_case_latency_ms"]
    token_counts = performance["token_counts"]
    return {
        "task_success": round(quality["task_success_rate"] * 100, 2),
        "tool_selection_accuracy": round(quality["tool_selection_accuracy"] * 100, 2),
        "tool_argument_accuracy": round(quality["tool_argument_accuracy"] * 100, 2),
        "security_violations": summary["privacy_isolation"]["leakage_cases"],
        "average_latency_ms": total_latency["mean"],
        "p95_latency_ms": total_latency["p95"],
        "total_tokens": token_counts["total_tokens"],
        "retrieval_recall_at_1": summary["retrieval"]["recall_at_1"],
        "retrieval_recall_at_3": summary["retrieval"]["recall_at_3"],
        "retrieval_recall_at_5": summary["retrieval"]["recall_at_5"],
        "mrr": summary["retrieval"]["mrr"],
        "ndcg": summary["retrieval"]["ndcg"],
        "citation_precision": None,
        "citation_recall": None,
        "groundedness": summary["hallucination"]["grounded_case_rate"],
        "unsupported_claim_rate": summary["hallucination"]["unsupported_claim_rate"],
        "embedding_latency": None,
        "reranking_latency": None,
        "rag_average_latency": None,
        "rag_p95_latency": None,
        "external_benchmark": summary,
    }


def expected_argument_projection(case: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    projected: dict[str, dict[str, Any]] = {}
    for expectation in case.get("expected_tool_arguments", []):
        tool = expectation["tool"]
        if tool in projected:
            return None
        projected[tool] = expectation.get("match", {})
    return projected or None


def trace_projection(
    record: dict[str, Any], benchmark_case: dict[str, Any]
) -> list[dict[str, Any]]:
    execution = record["execution"]
    traces: list[dict[str, Any]] = [
        {
            "event_type": "external_benchmark_expectation",
            "name": benchmark_case["id"],
            "payload": {
                "actual_input": benchmark_case["prompt"],
                "expected_tools": benchmark_case["required_tools"],
                "allowed_tools": benchmark_case["allowed_tools"],
                "expected_tool_arguments": benchmark_case["expected_tool_arguments"],
                "expected_facts": benchmark_case["expected_facts"],
                "expected_unknown": benchmark_case.get("expected_unknown", False),
                "gold_evidence_ids": benchmark_case["gold_evidence_ids"],
                "audit_classification": record["evaluation"].get("audit_classification"),
                "reason_codes": record["evaluation"]["reason_codes"],
            },
            "duration_ms": None,
        }
    ]
    for turn in execution.get("turns", []):
        traces.append(
            {
                "event_type": "external_turn_completed",
                "name": None,
                "payload": turn,
                "duration_ms": turn.get("latency_ms"),
            }
        )
    for message in execution.get("messages", []):
        traces.append(
            {
                "event_type": f"{message.get('role', 'unknown')}_message",
                "name": message.get("name"),
                "payload": message,
                "duration_ms": None,
            }
        )
    for call in execution.get("tool_calls", []):
        traces.append(
            {
                "event_type": "tool_call_observed",
                "name": call.get("name"),
                "payload": call,
                "duration_ms": call.get("tool_latency_ms"),
            }
        )
    for retrieval in execution.get("retrievals", []):
        traces.append(
            {
                "event_type": "external_retrieval_observed",
                "name": "search_vector_knowledge_base",
                "payload": retrieval,
                "duration_ms": None,
            }
        )
    for claim in record["evaluation"].get("claims", []):
        traces.append(
            {
                "event_type": "claim_extracted",
                "name": claim.get("kind"),
                "payload": claim,
                "duration_ms": None,
            }
        )
    if execution.get("isolation"):
        traces.append(
            {
                "event_type": "privacy_isolation_checked",
                "name": "synthetic_canary",
                "payload": execution["isolation"],
                "duration_ms": None,
            }
        )
    return traces


def evaluation_projection(record: dict[str, Any]) -> list[dict[str, Any]]:
    evaluation = record["evaluation"]
    metrics = evaluation["metrics"]
    projected = [
        {
            "metric": "task_success",
            "value": float(metrics["task_success"]),
            "passed": bool(metrics["task_success"]),
            "reason_code": evaluation["reason_codes"][0]
            if not metrics["task_success"] and evaluation["reason_codes"]
            else None,
            "explanation": "Audited deterministic external-benchmark task outcome.",
            "expected": True,
            "actual": bool(metrics["task_success"]),
            "evidence": {"audit_classification": evaluation.get("audit_classification")},
        },
        {
            "metric": "tool_selection",
            "value": metrics["tool_selection_accuracy"],
            "passed": metrics["tool_selection_accuracy"] == 1.0,
            "reason_code": "TOOL_SELECTION_MISMATCH"
            if metrics["tool_selection_accuracy"] != 1.0
            else None,
            "explanation": "Required and unexpected tool calls evaluated from the observed trace.",
            "expected": 1.0,
            "actual": metrics["tool_selection_accuracy"],
            "evidence": {
                "required_tool_recall": metrics["required_tool_recall"],
                "unexpected_tool_rate": metrics["unexpected_tool_rate"],
            },
        },
        {
            "metric": "tool_arguments",
            "value": metrics["tool_argument_accuracy"],
            "passed": metrics["tool_argument_accuracy"] == 1.0,
            "reason_code": "TOOL_ARGUMENT_MISMATCH"
            if metrics["tool_argument_accuracy"] != 1.0
            else None,
            "explanation": "Observed arguments evaluated against pinned source-derived expectations.",
            "expected": 1.0,
            "actual": metrics["tool_argument_accuracy"],
            "evidence": {"required_argument_accuracy": metrics["required_argument_accuracy"]},
        },
        {
            "metric": "groundedness",
            "value": float(metrics["grounded_case"]),
            "passed": bool(metrics["grounded_case"]),
            "reason_code": "UNGROUNDED_CLAIM" if not metrics["grounded_case"] else None,
            "explanation": "Deterministically extractable claims checked against same-case evidence.",
            "expected": True,
            "actual": bool(metrics["grounded_case"]),
            "evidence": {"claims": evaluation.get("claims", [])},
        },
        {
            "metric": "unsupported_claim_rate",
            "value": metrics["unsupported_claim_rate"],
            "passed": metrics["unsupported_claim_rate"] == 0.0,
            "reason_code": "UNSUPPORTED_FACTUAL_CLAIM"
            if metrics["unsupported_claim_rate"] != 0.0
            else None,
            "explanation": "Unsupported and fabricated values divided by extracted factual claims.",
            "expected": 0.0,
            "actual": metrics["unsupported_claim_rate"],
            "evidence": {"claim_scope": metrics["claim_scope"]},
        },
    ]
    for finding in evaluation["findings"]:
        projected.append(
            {
                "metric": finding["type"],
                "value": 0.0,
                "passed": False,
                "reason_code": finding["reason_code"],
                "explanation": finding["reason"],
                "expected": finding["expected_evidence"],
                "actual": finding["actual_claim"],
                "evidence": {
                    "actual_evidence": finding["actual_evidence"],
                    "source_reference": finding["source_reference"],
                    "audit_classification": finding["audit_classification"],
                },
            }
        )
    return projected


async def integrate(args: argparse.Namespace) -> dict[str, Any]:
    records = read_jsonl(args.records)
    summary = load_json(args.summary)
    cases = {case["id"]: case for case in load_json(ROOT / "cases.json")}
    manifest = load_json(ROOT / "manifest.yaml")
    source_run = load_json(args.source_run)
    if not summary.get("audit", {}).get("completed"):
        raise RuntimeError("Only fully audited benchmark results may be integrated")
    if len(records) != source_run["selected_execution_count"]:
        raise RuntimeError("Audited record count does not match the source run")

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == args.owner_email))
        if user is None:
            raise RuntimeError(f"Axiom owner does not exist: {args.owner_email}")
        membership = await session.scalar(
            select(OrganizationMember).where(OrganizationMember.user_id == user.id)
        )
        if membership is None:
            raise RuntimeError("Axiom owner has no organization membership")

        project = await session.scalar(
            select(Project).where(
                Project.organization_id == membership.organization_id,
                Project.name == PROJECT_NAME,
            )
        )
        if project is None:
            project = Project(
                organization_id=membership.organization_id,
                name=PROJECT_NAME,
                description="Audited third-party agent benchmarks with pinned provenance.",
            )
            session.add(project)
            await session.flush()

        agent = await session.scalar(
            select(Agent).where(Agent.project_id == project.id, Agent.name == AGENT_NAME)
        )
        if agent is None:
            agent = Agent(
                project_id=project.id,
                name=AGENT_NAME,
                description="Pinned external LangGraph customer-support agent.",
            )
            session.add(agent)
            await session.flush()

        version_name = manifest["upstream"]["commit"]
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.version == version_name,
            )
        )
        version_values = {
            "adapter_type": "generic_http",
            "endpoint_url": source_run["base_url"],
            "model_provider": "ollama",
            "model_name": manifest["model"]["name"],
            "system_prompt": "Pinned upstream prompt; see immutable external repository SHA.",
            "config": {
                "external_repository": manifest["upstream"]["repository"],
                "pinned_sha": version_name,
                "benchmark_id": manifest["benchmark_id"],
            },
            "tool_registry": [
                {"name": name, **definition} for name, definition in manifest["tools"].items()
            ],
        }
        if version is None:
            version = AgentVersion(agent_id=agent.id, version=version_name, **version_values)
            session.add(version)
            await session.flush()
        else:
            for key, value in version_values.items():
                setattr(version, key, value)

        suite = await session.scalar(
            select(TestSuite).where(
                TestSuite.project_id == project.id,
                TestSuite.name == SUITE_NAME,
            )
        )
        if suite is None:
            suite = TestSuite(
                project_id=project.id,
                name=SUITE_NAME,
                description="100-case deterministic external benchmark with audited evidence.",
                version="v1",
                gate_policy={"audit_required": True, "false_positives_permitted": 0},
            )
            session.add(suite)
            await session.flush()

        existing_scenarios = {
            scenario.name: scenario
            for scenario in (
                await session.scalars(select(Scenario).where(Scenario.test_suite_id == suite.id))
            ).all()
        }
        scenarios: dict[str, Scenario] = {}
        for case_id in source_run["selected_case_ids"]:
            case = cases[case_id]
            scenario = existing_scenarios.get(case_id)
            values = {
                "input": case["prompt"],
                "expected_output": None,
                "expected_tools": case["required_tools"],
                "forbidden_tools": sorted(set(manifest["tools"]) - set(case["allowed_tools"])),
                "expected_tool_arguments": expected_argument_projection(case),
                "tags": ["external_benchmark", case["category"], case["difficulty"]],
                "severity": {
                    "easy": Severity.LOW,
                    "medium": Severity.MEDIUM,
                    "hard": Severity.HIGH,
                }[case["difficulty"]],
                "timeout_seconds": int(source_run["timeout_seconds"]),
                "scenario_metadata": {
                    "benchmark_case_id": case_id,
                    "expected_facts": case["expected_facts"],
                    "gold_evidence_ids": case["gold_evidence_ids"],
                    "full_expected_tool_arguments": case["expected_tool_arguments"],
                    "read_only": case["read_only"],
                },
            }
            if scenario is None:
                scenario = Scenario(test_suite_id=suite.id, name=case_id, **values)
                session.add(scenario)
                await session.flush()
            else:
                for key, value in values.items():
                    setattr(scenario, key, value)
            scenarios[case_id] = scenario

        source_run_id = source_run["run_id"]
        existing_runs = (
            await session.scalars(
                select(Run).where(
                    Run.project_id == project.id,
                    Run.test_suite_id == suite.id,
                    Run.agent_version_id == version.id,
                )
            )
        ).all()
        run = next(
            (
                item
                for item in existing_runs
                if item.snapshot.get("external_benchmark", {}).get("source_run_id") == source_run_id
            ),
            None,
        )
        verdict_counts = summary["verdicts"]
        run_verdict = (
            Verdict.BLOCK
            if verdict_counts["block"]
            else Verdict.WARN
            if verdict_counts["warn"]
            else Verdict.PASS
        )
        snapshot = {
            "agent": {
                "id": str(agent.id),
                "version_id": str(version.id),
                "version": version_name,
                "adapter_type": "generic_http",
                "model_provider": "ollama",
                "model_name": manifest["model"]["name"],
            },
            "suite": {"id": str(suite.id), "version": "v1"},
            "scenario_ids": [
                str(scenarios[case_id].id) for case_id in source_run["selected_case_ids"]
            ],
            "scenarios": [
                {"id": str(scenarios[case_id].id), "name": case_id}
                for case_id in source_run["selected_case_ids"]
            ],
            "external_benchmark": {
                "benchmark_id": manifest["benchmark_id"],
                "source_run_id": source_run_id,
                "external_repository": manifest["upstream"]["repository"],
                "pinned_sha": version_name,
                "audit_completed": True,
                "raw_result_directory": args.records.parent.as_posix(),
            },
        }
        run_values = {
            "status": RunStatus.COMPLETED,
            "verdict": run_verdict,
            "snapshot": snapshot,
            "metrics": product_metrics(summary),
            "overall_score": Decimal(
                str(sum(record["evaluation"]["score"] for record in records) / len(records))
            ),
            "total_cases": len(records),
            "completed_cases": len(records),
            "passed_cases": verdict_counts["pass"],
            "failed_cases": verdict_counts["warn"] + verdict_counts["block"],
            "started_at": datetime.fromisoformat(source_run["started_at"]),
            "finished_at": datetime.fromisoformat(source_run["finished_at"]),
        }
        if run is None:
            run = Run(
                project_id=project.id,
                test_suite_id=suite.id,
                agent_version_id=version.id,
                **run_values,
            )
            session.add(run)
            await session.flush()
        else:
            for key, value in run_values.items():
                setattr(run, key, value)

        existing_case_results = {
            item.scenario_id: item
            for item in (
                await session.scalars(select(CaseResult).where(CaseResult.run_id == run.id))
            ).all()
        }
        for record in records:
            case_id = record["case"]["id"]
            scenario = scenarios[case_id]
            case_result = existing_case_results.get(scenario.id)
            usage = numeric_usage(record["execution"])
            result_values = {
                "status": CaseStatus.COMPLETED,
                "verdict": Verdict(record["evaluation"]["verdict"]),
                "score": Decimal(str(record["evaluation"]["score"])),
                "reason_codes": record["evaluation"]["reason_codes"],
                "final_response": record["execution"]["final_response"],
                "latency_ms": int(record["execution"]["performance"]["total_case_latency_ms"]),
                **usage,
                "estimated_cost": None,
                "started_at": datetime.fromisoformat(record["started_at"]),
                "finished_at": datetime.fromisoformat(record["completed_at"]),
            }
            if case_result is None:
                case_result = CaseResult(run_id=run.id, scenario_id=scenario.id, **result_values)
                session.add(case_result)
                await session.flush()
            else:
                for key, value in result_values.items():
                    setattr(case_result, key, value)
                await session.execute(delete(Trace).where(Trace.case_result_id == case_result.id))
                await session.execute(
                    delete(EvalResult).where(EvalResult.case_result_id == case_result.id)
                )
            for sequence, trace in enumerate(trace_projection(record, cases[case_id]), 1):
                session.add(Trace(case_result_id=case_result.id, sequence_number=sequence, **trace))
            for evaluation in evaluation_projection(record):
                session.add(EvalResult(case_result_id=case_result.id, **evaluation))

        await session.commit()
        resource_counts = {
            "projects": int(
                await session.scalar(
                    select(func.count(Project.id)).where(
                        Project.organization_id == membership.organization_id,
                        Project.name == PROJECT_NAME,
                    )
                )
                or 0
            ),
            "agents": int(
                await session.scalar(
                    select(func.count(Agent.id)).where(
                        Agent.project_id == project.id,
                        Agent.name == AGENT_NAME,
                    )
                )
                or 0
            ),
            "agent_versions": int(
                await session.scalar(
                    select(func.count(AgentVersion.id)).where(
                        AgentVersion.agent_id == agent.id,
                        AgentVersion.version == version_name,
                    )
                )
                or 0
            ),
            "suites": int(
                await session.scalar(
                    select(func.count(TestSuite.id)).where(
                        TestSuite.project_id == project.id,
                        TestSuite.name == SUITE_NAME,
                    )
                )
                or 0
            ),
            "scenarios": int(
                await session.scalar(
                    select(func.count(Scenario.id)).where(Scenario.test_suite_id == suite.id)
                )
                or 0
            ),
            "matching_runs": sum(
                item.snapshot.get("external_benchmark", {}).get("source_run_id") == source_run_id
                for item in existing_runs
            )
            + (1 if run not in existing_runs else 0),
            "case_results": int(
                await session.scalar(
                    select(func.count(CaseResult.id)).where(CaseResult.run_id == run.id)
                )
                or 0
            ),
            "traces": int(
                await session.scalar(
                    select(func.count(Trace.id))
                    .join(CaseResult, Trace.case_result_id == CaseResult.id)
                    .where(CaseResult.run_id == run.id)
                )
                or 0
            ),
            "evaluations": int(
                await session.scalar(
                    select(func.count(EvalResult.id))
                    .join(CaseResult, EvalResult.case_result_id == CaseResult.id)
                    .where(CaseResult.run_id == run.id)
                )
                or 0
            ),
        }
        expected_counts = {
            "projects": 1,
            "agents": 1,
            "agent_versions": 1,
            "suites": 1,
            "scenarios": len(source_run["selected_case_ids"]),
            "matching_runs": 1,
            "case_results": len(records),
            "traces": sum(
                len(trace_projection(record, cases[record["case"]["id"]])) for record in records
            ),
            "evaluations": sum(len(evaluation_projection(record)) for record in records),
        }
        return {
            "project": {"id": str(project.id), "name": project.name},
            "agent": {"id": str(agent.id), "name": agent.name},
            "agent_version": {
                "id": str(version.id),
                "version": version.version,
                "model": version.model_name,
            },
            "suite": {"id": str(suite.id), "name": suite.name},
            "run": {"id": str(run.id), "case_count": len(records)},
            "dashboard_url": f"http://localhost:3000/runs/{run.id}",
            "resource_counts": resource_counts,
            "expected_counts": expected_counts,
            "resource_counts_valid": resource_counts == expected_counts,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an audited external run into Axiom")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--owner-email", default="demo@agentarena.dev")
    parser.add_argument("--evidence-output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(integrate(args))
    if args.evidence_output:
        prior_attempts: list[dict[str, Any]] = []
        if args.evidence_output.exists():
            prior = load_json(args.evidence_output)
            prior_attempts = prior.get("import_attempts", [])
        attempts = [*prior_attempts, result]
        stable_keys = ("project", "agent", "agent_version", "suite", "run")
        same_ids = len(attempts) >= 2 and all(
            attempts[-2][key]["id"] == attempts[-1][key]["id"] for key in stable_keys
        )
        evidence = {
            **result,
            "schema_version": 1,
            "import_attempts": attempts[-2:],
            "idempotency_verified": (
                same_ids
                and attempts[-2]["resource_counts_valid"]
                and attempts[-1]["resource_counts_valid"]
                and attempts[-2]["resource_counts"] == attempts[-1]["resource_counts"]
            ),
        }
        args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        result = evidence
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
