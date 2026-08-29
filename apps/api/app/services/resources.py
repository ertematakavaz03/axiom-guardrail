from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import (
    Agent,
    AgentVersion,
    OrganizationMember,
    Project,
    Scenario,
    TestSuite,
    User,
)
from apps.api.app.errors import ConflictError
from apps.api.app.repositories.scoping import get_agent, get_project, get_scenario, get_suite
from apps.api.app.schemas.resources import (
    AgentCreate,
    AgentVersionCreate,
    ProjectCreate,
    ProjectUpdate,
    ScenarioCreate,
    ScenarioUpdate,
    SuiteCreate,
    SuiteUpdate,
)
from apps.api.app.services.audit import add_audit
from services.tool_gateway.registry import DEMO_TOOL_REGISTRY


class ResourceService:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def list_projects(self) -> Sequence[Project]:
        result = await self.session.scalars(
            select(Project)
            .join(OrganizationMember, OrganizationMember.organization_id == Project.organization_id)
            .where(OrganizationMember.user_id == self.user.id)
            .order_by(Project.created_at.desc())
        )
        return result.all()

    async def create_project(self, payload: ProjectCreate) -> Project:
        organization_id = await self.session.scalar(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == self.user.id)
            .order_by(OrganizationMember.created_at)
        )
        if organization_id is None:
            raise ConflictError("User has no organization membership")
        project = Project(organization_id=organization_id, **payload.model_dump())
        self.session.add(project)
        await self.session.flush()
        add_audit(
            self.session,
            organization_id=organization_id,
            user_id=self.user.id,
            project_id=project.id,
            action="project.create",
            resource_type="project",
            resource_id=project.id,
        )
        await self.session.commit()
        return project

    async def update_project(self, project_id: uuid.UUID, payload: ProjectUpdate) -> Project:
        project = await get_project(self.session, self.user.id, project_id)
        self._apply(project, payload.model_dump(exclude_none=True))
        self._audit(project, "project.update", "project", project.id)
        await self.session.commit()
        return project

    async def delete_project(self, project_id: uuid.UUID) -> None:
        project = await get_project(self.session, self.user.id, project_id)
        self._audit(project, "project.delete", "project", project.id)
        await self.session.flush()
        await self.session.delete(project)
        await self.session.commit()

    async def list_agents(self, project_id: uuid.UUID) -> Sequence[Agent]:
        await get_project(self.session, self.user.id, project_id)
        return (
            await self.session.scalars(select(Agent).where(Agent.project_id == project_id))
        ).all()

    async def create_agent(self, project_id: uuid.UUID, payload: AgentCreate) -> Agent:
        project = await get_project(self.session, self.user.id, project_id)
        agent = Agent(project_id=project.id, **payload.model_dump())
        self.session.add(agent)
        await self.session.flush()
        self._audit(project, "agent.create", "agent", agent.id)
        await self.session.commit()
        return agent

    async def create_version(
        self, agent_id: uuid.UUID, payload: AgentVersionCreate
    ) -> AgentVersion:
        agent = await get_agent(self.session, self.user.id, agent_id)
        if await self.session.scalar(
            select(AgentVersion.id).where(
                AgentVersion.agent_id == agent.id, AgentVersion.version == payload.version
            )
        ):
            raise ConflictError("Agent version already exists")
        data = payload.model_dump(mode="json")
        if data["adapter_type"] == "demo_support_agent" and not data["tool_registry"]:
            data["tool_registry"] = DEMO_TOOL_REGISTRY
        version = AgentVersion(agent_id=agent.id, **data)
        self.session.add(version)
        await self.session.flush()
        project = await get_project(self.session, self.user.id, agent.project_id)
        self._audit(project, "agent_version.create", "agent_version", version.id)
        await self.session.commit()
        return version

    async def list_versions(self, agent_id: uuid.UUID) -> Sequence[AgentVersion]:
        agent = await get_agent(self.session, self.user.id, agent_id)
        return (
            await self.session.scalars(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.created_at.desc())
            )
        ).all()

    async def list_suites(self, project_id: uuid.UUID) -> Sequence[TestSuite]:
        await get_project(self.session, self.user.id, project_id)
        return (
            await self.session.scalars(select(TestSuite).where(TestSuite.project_id == project_id))
        ).all()

    async def create_suite(self, project_id: uuid.UUID, payload: SuiteCreate) -> TestSuite:
        project = await get_project(self.session, self.user.id, project_id)
        suite = TestSuite(project_id=project.id, **payload.model_dump())
        self.session.add(suite)
        await self.session.flush()
        self._audit(project, "suite.create", "test_suite", suite.id)
        await self.session.commit()
        return suite

    async def update_suite(self, suite_id: uuid.UUID, payload: SuiteUpdate) -> TestSuite:
        suite = await get_suite(self.session, self.user.id, suite_id)
        self._apply(suite, payload.model_dump(exclude_none=True))
        project = await get_project(self.session, self.user.id, suite.project_id)
        self._audit(project, "suite.update", "test_suite", suite.id)
        await self.session.commit()
        return suite

    async def list_scenarios(self, suite_id: uuid.UUID) -> Sequence[Scenario]:
        suite = await get_suite(self.session, self.user.id, suite_id)
        return (
            await self.session.scalars(
                select(Scenario)
                .where(Scenario.test_suite_id == suite.id)
                .order_by(Scenario.created_at)
            )
        ).all()

    async def create_scenario(self, suite_id: uuid.UUID, payload: ScenarioCreate) -> Scenario:
        suite = await get_suite(self.session, self.user.id, suite_id)
        data = payload.model_dump()
        data["scenario_metadata"] = data.pop("metadata")
        scenario = Scenario(test_suite_id=suite.id, **data)
        self.session.add(scenario)
        await self.session.flush()
        project = await get_project(self.session, self.user.id, suite.project_id)
        self._audit(project, "scenario.create", "scenario", scenario.id)
        await self.session.commit()
        return scenario

    async def update_scenario(self, scenario_id: uuid.UUID, payload: ScenarioUpdate) -> Scenario:
        scenario = await get_scenario(self.session, self.user.id, scenario_id)
        data = payload.model_dump(exclude_unset=True)
        nullable_fields = {"expected_output", "expected_tool_arguments"}
        data = {
            key: value for key, value in data.items() if value is not None or key in nullable_fields
        }
        if "metadata" in data:
            data["scenario_metadata"] = data.pop("metadata")
        self._apply(scenario, data)
        suite = await get_suite(self.session, self.user.id, scenario.test_suite_id)
        project = await get_project(self.session, self.user.id, suite.project_id)
        self._audit(project, "scenario.update", "scenario", scenario.id)
        await self.session.commit()
        return scenario

    async def delete_scenario(self, scenario_id: uuid.UUID) -> None:
        scenario = await get_scenario(self.session, self.user.id, scenario_id)
        suite = await get_suite(self.session, self.user.id, scenario.test_suite_id)
        project = await get_project(self.session, self.user.id, suite.project_id)
        self._audit(project, "scenario.delete", "scenario", scenario.id)
        await self.session.flush()
        await self.session.delete(scenario)
        await self.session.commit()

    def _audit(
        self, project: Project, action: str, resource_type: str, resource_id: uuid.UUID
    ) -> None:
        add_audit(
            self.session,
            organization_id=project.organization_id,
            user_id=self.user.id,
            project_id=project.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    @staticmethod
    def _apply(model: Any, values: dict[str, Any]) -> None:
        for key, value in values.items():
            setattr(model, key, value)
