from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import (
    CaseResult,
    EvalResult,
    MCPRegistration,
    SecurityPolicyRecord,
    User,
)
from apps.api.app.db.session import get_session
from apps.api.app.repositories.scoping import get_agent, get_case, get_project, get_run
from apps.api.app.security.auth import get_current_user
from apps.api.app.services.audit import add_audit
from services.security.mcp import MCPInventory, inspect_inventory
from services.security.models import SecurityPolicy, SecurityScenario, StrictModel
from services.security.policy import PolicyEngine
from services.security.reasons import REASONS

router = APIRouter(tags=["Security and red team"])


@router.post("/projects/{project_id}/security/demo", status_code=201)
async def install_demo(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    await get_project(session, user.id, project_id)
    from demos.security_lab.seed import seed

    try:
        return await seed(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class PolicyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    agent_id: uuid.UUID | None = None
    policy: SecurityPolicy


class InventoryCreate(StrictModel):
    inventory: MCPInventory
    approved: bool = False


@router.get("/security/reason-codes")
async def reason_codes(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {code: reason.model_dump(mode="json") for code, reason in REASONS.items()}


@router.get("/security/scenario-schema")
async def scenario_schema(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return SecurityScenario.model_json_schema()


@router.get("/security/corpus", response_model=list[SecurityScenario])
async def scenario_corpus(user: User = Depends(get_current_user)) -> list[SecurityScenario]:
    from demos.security_lab.corpus import corpus

    return corpus()


@router.post("/projects/{project_id}/security/policies", status_code=201)
async def create_policy(
    project_id: uuid.UUID,
    payload: PolicyCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    project = await get_project(session, user.id, project_id)
    if payload.agent_id:
        agent = await get_agent(session, user.id, payload.agent_id)
        if agent.project_id != project_id:
            raise HTTPException(409, "Agent must belong to policy project")
    try:
        engine = PolicyEngine(payload.policy)
    except Exception as exc:
        raise HTTPException(422, "Invalid or unsupported policy schema") from exc
    record = SecurityPolicyRecord(
        project_id=project_id,
        agent_id=payload.agent_id,
        name=payload.name,
        policy=payload.policy.model_dump(mode="json"),
        policy_hash=engine.policy_hash,
    )
    session.add(record)
    await session.flush()
    add_audit(
        session,
        organization_id=project.organization_id,
        user_id=user.id,
        project_id=project_id,
        action="security.policy.create",
        resource_type="security_policy",
        resource_id=record.id,
        metadata={"policy_hash": record.policy_hash},
    )
    await session.commit()
    return {
        "id": str(record.id),
        "name": record.name,
        "policy": record.policy,
        "policy_hash": record.policy_hash,
    }


@router.get("/projects/{project_id}/security/policies")
async def list_policies(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    await get_project(session, user.id, project_id)
    records = (
        await session.scalars(
            select(SecurityPolicyRecord)
            .where(SecurityPolicyRecord.project_id == project_id)
            .order_by(SecurityPolicyRecord.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "agent_id": str(r.agent_id) if r.agent_id else None,
            "policy": r.policy,
            "policy_hash": r.policy_hash,
        }
        for r in records
    ]


@router.post("/projects/{project_id}/security/mcp", status_code=201)
async def register_inventory(
    project_id: uuid.UUID,
    payload: InventoryCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    project = await get_project(session, user.id, project_id)
    previous = await session.scalar(
        select(MCPRegistration)
        .where(
            MCPRegistration.project_id == project_id,
            MCPRegistration.server == payload.inventory.server,
            MCPRegistration.approved.is_(True),
        )
        .order_by(MCPRegistration.created_at.desc())
        .limit(1)
    )
    issues = inspect_inventory(
        payload.inventory, MCPInventory.model_validate(previous.inventory) if previous else None
    )
    record = MCPRegistration(
        project_id=project_id,
        server=payload.inventory.server,
        inventory=payload.inventory.model_dump(mode="json"),
        inventory_hash=payload.inventory.fingerprint(),
        approved=payload.approved,
    )
    session.add(record)
    await session.flush()
    add_audit(
        session,
        organization_id=project.organization_id,
        user_id=user.id,
        project_id=project_id,
        action="security.mcp.approve" if payload.approved else "security.mcp.observe",
        resource_type="mcp_registration",
        resource_id=record.id,
        metadata={"inventory_hash": record.inventory_hash, "issues": issues},
    )
    await session.commit()
    return {
        "id": str(record.id),
        "server": record.server,
        "approved": record.approved,
        "inventory_hash": record.inventory_hash,
        "issues": issues,
    }


@router.get("/projects/{project_id}/security/mcp")
async def list_inventories(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    await get_project(session, user.id, project_id)
    records = (
        await session.scalars(
            select(MCPRegistration)
            .where(MCPRegistration.project_id == project_id)
            .order_by(MCPRegistration.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "server": r.server,
            "inventory": r.inventory,
            "inventory_hash": r.inventory_hash,
            "approved": r.approved,
        }
        for r in records
    ]


@router.get("/runs/{run_id}/security")
async def run_security(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    run = await get_run(session, user.id, run_id)
    results = (
        await session.scalars(
            select(EvalResult)
            .join(CaseResult, CaseResult.id == EvalResult.case_result_id)
            .where(CaseResult.run_id == run_id, EvalResult.metric == "security_summary")
        )
    ).all()
    return {
        "run_id": str(run.id),
        "status": run.status.value,
        "metrics": run.metrics.get("security"),
        "cases": [{"case_id": str(r.case_result_id), **r.evidence} for r in results],
    }


@router.get("/cases/{case_id}/security")
async def case_security(
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    await get_case(session, user.id, case_id)
    result = await session.scalar(
        select(EvalResult).where(
            EvalResult.case_result_id == case_id, EvalResult.metric == "security_summary"
        )
    )
    return result.evidence if result else {}
