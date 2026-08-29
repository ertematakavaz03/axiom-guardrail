from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import select

from apps.api.app.config import Settings, get_settings
from apps.api.app.db.models import (
    CaseResult,
    CaseStatus,
    EvalResult,
    Run,
    RunStatus,
    Trace,
    Verdict,
)
from apps.api.app.db.session import SessionLocal
from apps.api.app.logging import configure_logging
from apps.api.app.security.redaction import redact_secrets
from services.evaluators.verdicts import aggregate_run
from services.observability.langfuse import LangfusePilot
from services.orchestrator.graph import CaseOrchestrator

configure_logging()
logger = logging.getLogger(__name__)


async def execute_run(ctx: dict[str, Any], run_id: str) -> None:
    settings: Settings = ctx.get("settings") or get_settings()
    parsed_id = uuid.UUID(run_id)
    async with SessionLocal() as session:
        run = await session.get(Run, parsed_id, with_for_update=True)
        if run is None or run.status != RunStatus.QUEUED:
            return
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        case_work: list[tuple[uuid.UUID, dict[str, Any]]] = []
        for scenario in run.snapshot["scenarios"]:
            case = CaseResult(run_id=run.id, scenario_id=uuid.UUID(scenario["id"]))
            session.add(case)
            await session.flush()
            case_work.append((case.id, scenario))
        await session.commit()

    queue: asyncio.Queue[tuple[uuid.UUID, dict[str, Any]]] = asyncio.Queue()
    for work in case_work:
        queue.put_nowait(work)

    async def consume() -> None:
        while not queue.empty():
            try:
                case_id, scenario = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await _execute_case(parsed_id, case_id, scenario, settings)
            finally:
                queue.task_done()

    concurrency = min(settings.worker_concurrency, len(case_work))
    workers = [asyncio.create_task(consume()) for _ in range(concurrency)]
    await queue.join()
    await asyncio.gather(*workers)
    await _finalize_run(parsed_id)


async def _execute_case(
    run_id: uuid.UUID, case_id: uuid.UUID, scenario: dict[str, Any], settings: Settings
) -> None:
    async with SessionLocal() as session:
        case = await session.get(CaseResult, case_id)
        run = await session.get(Run, run_id)
        if case is None or run is None:
            return
        case.status = CaseStatus.RUNNING
        case.started_at = datetime.now(UTC)
        await session.commit()
        snapshot = run.snapshot

    try:
        result = await CaseOrchestrator(settings).run_case(
            run_id=str(run_id),
            case_id=str(case_id),
            scenario=scenario,
            agent_config=snapshot["agent"],
            suite_config=snapshot["suite"],
            budget=snapshot["budget"],
        )
        async with SessionLocal() as session:
            case = await session.get(CaseResult, case_id, with_for_update=True)
            if case is None:
                return
            case.status = CaseStatus.COMPLETED
            case.verdict = Verdict(result.verdict)
            case.score = Decimal(str(result.score))
            case.reason_codes = result.reason_codes
            case.final_response = result.final_response
            case.latency_ms = result.latency_ms
            case.input_tokens = result.input_tokens
            case.output_tokens = result.output_tokens
            case.total_tokens = result.total_tokens
            case.estimated_cost = Decimal(str(result.estimated_cost))
            case.finished_at = datetime.now(UTC)
            session.add_all(
                [
                    Trace(
                        case_result_id=case.id,
                        sequence_number=trace.sequence_number,
                        event_type=trace.event_type,
                        name=trace.name,
                        payload=trace.payload,
                        duration_ms=trace.duration_ms,
                        created_at=trace.occurred_at,
                    )
                    for trace in result.traces
                ]
                + [
                    EvalResult(
                        case_result_id=case.id,
                        metric=evaluation.metric,
                        value=Decimal(str(evaluation.value))
                        if evaluation.value is not None
                        else None,
                        passed=evaluation.passed,
                        reason_code=evaluation.reason_code,
                        explanation=evaluation.explanation,
                        expected=evaluation.expected,
                        actual=evaluation.actual,
                        evidence=evaluation.evidence,
                    )
                    for evaluation in result.evaluations
                ]
            )
            await session.commit()
        await asyncio.to_thread(
            LangfusePilot(settings).emit_case,
            run_id=str(run_id),
            case_id=str(case_id),
            scenario_name=str(scenario["name"]),
            result=result,
        )
    except Exception as exc:
        logger.exception("case.execution_failed", extra={"run_id": run_id, "case_id": case_id})
        async with SessionLocal() as session:
            case = await session.get(CaseResult, case_id, with_for_update=True)
            if case:
                case.status = CaseStatus.FAILED
                case.verdict = Verdict.BLOCK
                case.reason_codes = ["INTERNAL_EXECUTION_ERROR"]
                case.finished_at = datetime.now(UTC)
                session.add(
                    Trace(
                        case_result_id=case.id,
                        sequence_number=1,
                        event_type="error",
                        payload=redact_secrets(
                            {
                                "reason_code": "INTERNAL_EXECUTION_ERROR",
                                "explanation": "Case execution failed safely",
                                "exception_type": type(exc).__name__,
                            }
                        ),
                    )
                )
                await session.commit()
    await _update_progress(run_id)


async def _update_progress(run_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id, with_for_update=True)
        if run is None:
            return
        cases = (await session.scalars(select(CaseResult).where(CaseResult.run_id == run_id))).all()
        run.completed_cases = sum(
            case.status in {CaseStatus.COMPLETED, CaseStatus.FAILED} for case in cases
        )
        run.passed_cases = sum(case.verdict == Verdict.PASS for case in cases)
        run.failed_cases = sum(case.verdict in {Verdict.WARN, Verdict.BLOCK} for case in cases)
        await session.commit()


async def _finalize_run(run_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id, with_for_update=True)
        if run is None:
            return
        cases = (await session.scalars(select(CaseResult).where(CaseResult.run_id == run_id))).all()
        summaries: list[dict[str, Any]] = []
        for case in cases:
            evaluations = (
                await session.scalars(
                    select(EvalResult).where(EvalResult.case_result_id == case.id)
                )
            ).all()
            summaries.append(
                {
                    "verdict": case.verdict.value if case.verdict else "block",
                    "latency_ms": case.latency_ms,
                    "total_tokens": case.total_tokens,
                    "estimated_cost": float(case.estimated_cost or 0),
                    "reason_codes": case.reason_codes,
                    "evaluations": [
                        {
                            "case_id": str(case.id),
                            "metric": evaluation.metric,
                            "passed": evaluation.passed,
                        }
                        for evaluation in evaluations
                    ],
                }
            )
        metrics, score, verdict = aggregate_run(summaries)
        run.metrics = metrics
        run.overall_score = Decimal(str(score))
        run.verdict = Verdict(verdict)
        run.status = RunStatus.COMPLETED
        run.finished_at = datetime.now(UTC)
        run.completed_cases = len(cases)
        run.passed_cases = sum(case.verdict == Verdict.PASS for case in cases)
        run.failed_cases = len(cases) - run.passed_cases
        await session.commit()
    await asyncio.to_thread(
        LangfusePilot(get_settings()).emit_run,
        run_id=str(run_id),
        metrics=metrics,
        verdict=verdict,
    )


class WorkerSettings:
    settings = get_settings()
    functions = [execute_run]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.worker_concurrency
    job_timeout = 3600
    keep_result = 3600

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        ctx["settings"] = get_settings()
