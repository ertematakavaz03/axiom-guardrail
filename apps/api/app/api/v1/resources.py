from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import User
from apps.api.app.db.session import get_session
from apps.api.app.repositories.scoping import get_agent, get_project, get_suite
from apps.api.app.schemas.resources import (
    AgentCreate,
    AgentResponse,
    AgentVersionCreate,
    AgentVersionResponse,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    ScenarioCreate,
    ScenarioResponse,
    ScenarioUpdate,
    SuiteCreate,
    SuiteResponse,
    SuiteUpdate,
)
from apps.api.app.security.auth import get_current_user
from apps.api.app.services.resources import ResourceService

router = APIRouter(tags=["Projects, agents, and suites"])


def service(session: AsyncSession, user: User) -> ResourceService:
    return ResourceService(session, user)


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(
    session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
) -> object:
    return await service(session, user).list_projects()


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).create_project(payload)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def project_detail(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await get_project(session, user.id, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).update_project(project_id, payload)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    await service(session, user).delete_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/agents", response_model=list[AgentResponse])
async def list_agents(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).list_agents(project_id)


@router.post(
    "/projects/{project_id}/agents",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    project_id: uuid.UUID,
    payload: AgentCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).create_agent(project_id, payload)


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def agent_detail(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await get_agent(session, user.id, agent_id)


@router.post(
    "/agents/{agent_id}/versions",
    response_model=AgentVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_version(
    agent_id: uuid.UUID,
    payload: AgentVersionCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).create_version(agent_id, payload)


@router.get("/agents/{agent_id}/versions", response_model=list[AgentVersionResponse])
async def list_agent_versions(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).list_versions(agent_id)


@router.get("/projects/{project_id}/suites", response_model=list[SuiteResponse])
async def list_suites(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).list_suites(project_id)


@router.post(
    "/projects/{project_id}/suites",
    response_model=SuiteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_suite(
    project_id: uuid.UUID,
    payload: SuiteCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).create_suite(project_id, payload)


@router.get("/suites/{suite_id}", response_model=SuiteResponse)
async def suite_detail(
    suite_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await get_suite(session, user.id, suite_id)


@router.patch("/suites/{suite_id}", response_model=SuiteResponse)
async def update_suite(
    suite_id: uuid.UUID,
    payload: SuiteUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).update_suite(suite_id, payload)


@router.get("/suites/{suite_id}/scenarios", response_model=list[ScenarioResponse])
async def list_scenarios(
    suite_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).list_scenarios(suite_id)


@router.post(
    "/suites/{suite_id}/scenarios",
    response_model=ScenarioResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    suite_id: uuid.UUID,
    payload: ScenarioCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).create_scenario(suite_id, payload)


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioResponse)
async def update_scenario(
    scenario_id: uuid.UUID,
    payload: ScenarioUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await service(session, user).update_scenario(scenario_id, payload)


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scenario(
    scenario_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    await service(session, user).delete_scenario(scenario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
