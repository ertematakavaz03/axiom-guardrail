from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import (
    Agent,
    AgentVersion,
    CaseResult,
    OrganizationMember,
    Project,
    Run,
    Scenario,
    TestSuite,
)
from apps.api.app.errors import ResourceNotFoundError


def _project_scope(user_id: uuid.UUID) -> Select[tuple[Project]]:
    return (
        select(Project)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(OrganizationMember.user_id == user_id)
    )


async def get_project(session: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = await session.scalar(_project_scope(user_id).where(Project.id == project_id))
    if project is None:
        raise ResourceNotFoundError("Project not found")
    return project


async def get_agent(session: AsyncSession, user_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = await session.scalar(
        select(Agent)
        .join(Project, Project.id == Agent.project_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(Agent.id == agent_id, OrganizationMember.user_id == user_id)
    )
    if agent is None:
        raise ResourceNotFoundError("Agent not found")
    return agent


async def get_agent_version(
    session: AsyncSession, user_id: uuid.UUID, version_id: uuid.UUID
) -> AgentVersion:
    version = await session.scalar(
        select(AgentVersion)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .join(Project, Project.id == Agent.project_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(AgentVersion.id == version_id, OrganizationMember.user_id == user_id)
    )
    if version is None:
        raise ResourceNotFoundError("Agent version not found")
    return version


async def get_suite(session: AsyncSession, user_id: uuid.UUID, suite_id: uuid.UUID) -> TestSuite:
    suite = await session.scalar(
        select(TestSuite)
        .join(Project, Project.id == TestSuite.project_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(TestSuite.id == suite_id, OrganizationMember.user_id == user_id)
    )
    if suite is None:
        raise ResourceNotFoundError("Test suite not found")
    return suite


async def get_scenario(
    session: AsyncSession, user_id: uuid.UUID, scenario_id: uuid.UUID
) -> Scenario:
    scenario = await session.scalar(
        select(Scenario)
        .join(TestSuite, TestSuite.id == Scenario.test_suite_id)
        .join(Project, Project.id == TestSuite.project_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(Scenario.id == scenario_id, OrganizationMember.user_id == user_id)
    )
    if scenario is None:
        raise ResourceNotFoundError("Scenario not found")
    return scenario


async def get_run(session: AsyncSession, user_id: uuid.UUID, run_id: uuid.UUID) -> Run:
    run = await session.scalar(
        select(Run)
        .join(Project, Project.id == Run.project_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(Run.id == run_id, OrganizationMember.user_id == user_id)
    )
    if run is None:
        raise ResourceNotFoundError("Run not found")
    return run


async def get_case(session: AsyncSession, user_id: uuid.UUID, case_id: uuid.UUID) -> CaseResult:
    case = await session.scalar(
        select(CaseResult)
        .join(Run, Run.id == CaseResult.run_id)
        .join(Project, Project.id == Run.project_id)
        .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
        .where(CaseResult.id == case_id, OrganizationMember.user_id == user_id)
    )
    if case is None:
        raise ResourceNotFoundError("Case result not found")
    return case
