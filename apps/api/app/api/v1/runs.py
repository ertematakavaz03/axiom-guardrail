from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.config import Settings, get_settings
from apps.api.app.db.models import CaseResult, EvalResult, Project, Run, RunStatus, Trace, User
from apps.api.app.db.session import get_session
from apps.api.app.repositories.scoping import get_case, get_project, get_run
from apps.api.app.schemas.resources import (
    CaseDetailResponse,
    CaseResponse,
    CaseTraceResponse,
    EvalResultResponse,
    RunCreate,
    RunEvent,
    RunResponse,
    TraceResponse,
)
from apps.api.app.security.auth import get_current_user
from apps.api.app.services.runs import ArqRunEnqueuer, RunService

router = APIRouter(tags=["Evaluation runs"])


def get_redis(request: Request) -> ArqRedis:
    return cast(ArqRedis, request.app.state.redis)


@router.post("/runs/{run_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    redis: ArqRedis = Depends(get_redis),
) -> dict[str, str]:
    await get_run(session, user.id, run_id)
    run = await session.get(Run, run_id, with_for_update=True)
    assert run is not None
    if "security" not in run.snapshot:
        raise HTTPException(409, "Recovery endpoint is for security runs")
    started = run.started_at
    if started and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    stale = (
        run.status == RunStatus.RUNNING
        and started is not None
        and started < datetime.now(UTC) - timedelta(seconds=3900)
    )
    if run.status != RunStatus.FAILED and not stale:
        raise HTTPException(
            409, "Run must have failed dispatch or exceed the worker timeout plus grace period"
        )
    run.status = RunStatus.QUEUED
    run.finished_at = None
    await session.commit()
    try:
        # A new queue id avoids a retained ARQ result suppressing a recovery job.
        await redis.enqueue_job(
            "execute_run", str(run_id), _job_id=f"resume:{run_id}:{uuid.uuid4()}"
        )
    except Exception:
        run.status = RunStatus.FAILED
        await session.commit()
        raise HTTPException(503, "Recovery queue unavailable") from None
    return {"run_id": str(run_id), "status": "queued"}


@router.post("/runs", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    redis: ArqRedis = Depends(get_redis),
) -> object:
    return await RunService(session, user, settings, ArqRunEnqueuer(redis)).create(payload)


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(
    project_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    # An explicit ownership subquery keeps the response scoped without trusting query parameters.
    from apps.api.app.db.models import OrganizationMember

    query = (
        select(Run)
        .join(Project, Project.id == Run.project_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(OrganizationMember.user_id == user.id)
        .order_by(Run.created_at.desc())
    )
    if project_id:
        await get_project(session, user.id, project_id)
        query = query.where(Run.project_id == project_id)
    return (await session.scalars(query)).all()


@router.get("/runs/{run_id}", response_model=RunResponse)
async def run_detail(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await get_run(session, user.id, run_id)


@router.get("/runs/{run_id}/cases", response_model=list[CaseResponse])
async def run_cases(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    await get_run(session, user.id, run_id)
    return (
        await session.scalars(
            select(CaseResult)
            .where(CaseResult.run_id == run_id)
            .order_by(CaseResult.started_at, CaseResult.id)
        )
    ).all()


@router.get("/cases/{case_result_id}", response_model=CaseDetailResponse)
async def case_detail(
    case_result_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    case = await get_case(session, user.id, case_result_id)
    evaluations = (
        await session.scalars(
            select(EvalResult)
            .where(EvalResult.case_result_id == case.id)
            .order_by(EvalResult.created_at)
        )
    ).all()
    return {**CaseResponse.model_validate(case).model_dump(), "evaluations": evaluations}


@router.get("/cases/{case_result_id}/trace", response_model=CaseTraceResponse)
async def case_trace(
    case_result_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CaseTraceResponse:
    case = await get_case(session, user.id, case_result_id)
    traces = (
        await session.scalars(
            select(Trace).where(Trace.case_result_id == case.id).order_by(Trace.sequence_number)
        )
    ).all()
    evaluations = (
        await session.scalars(
            select(EvalResult)
            .where(EvalResult.case_result_id == case.id)
            .order_by(EvalResult.created_at)
        )
    ).all()
    return CaseTraceResponse(
        case=CaseResponse.model_validate(case),
        traces=[TraceResponse.model_validate(trace) for trace in traces],
        evaluations=[EvalResultResponse.model_validate(item) for item in evaluations],
    )


@router.get("/runs/{run_id}/stream")
async def run_stream(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    await get_run(session, user.id, run_id)

    async def events() -> AsyncIterator[str]:
        while True:
            session.expire_all()
            run = await session.get(Run, run_id)
            if run is None:
                return
            event = RunEvent.model_validate(run)
            yield f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"
            if run.status.value in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")
