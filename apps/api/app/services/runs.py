from __future__ import annotations

import hashlib
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.config import Settings
from apps.api.app.db.models import Agent, AgentVersion, Run, RunStatus, Scenario, TestSuite, User
from apps.api.app.errors import ConflictError, QueueUnavailableError
from apps.api.app.repositories.scoping import get_agent_version, get_project, get_suite
from apps.api.app.schemas.resources import RunCreate
from apps.api.app.services.audit import add_audit
from services.evaluators.engine import DeterministicEvaluationEngine


class RunEnqueuer(Protocol):
    async def enqueue(self, run_id: uuid.UUID) -> None: ...


class ArqRunEnqueuer:
    def __init__(self, redis: ArqRedis) -> None:
        self.redis = redis

    async def enqueue(self, run_id: uuid.UUID) -> None:
        job = await self.redis.enqueue_job("execute_run", str(run_id), _job_id=f"run:{run_id}")
        if job is None:
            raise ConflictError("Run is already queued")


class RunService:
    def __init__(
        self,
        session: AsyncSession,
        user: User,
        settings: Settings,
        enqueuer: RunEnqueuer,
    ) -> None:
        self.session = session
        self.user = user
        self.settings = settings
        self.enqueuer = enqueuer

    async def create(self, payload: RunCreate) -> Run:
        project = await get_project(self.session, self.user.id, payload.project_id)
        suite = await get_suite(self.session, self.user.id, payload.test_suite_id)
        version = await get_agent_version(self.session, self.user.id, payload.agent_version_id)
        agent = await self.session.get(Agent, version.agent_id)
        if suite.project_id != project.id or agent is None or agent.project_id != project.id:
            raise ConflictError("Project, suite, and agent version must belong to the same project")
        scenarios = (
            await self.session.scalars(
                select(Scenario)
                .where(Scenario.test_suite_id == suite.id)
                .order_by(Scenario.created_at)
            )
        ).all()
        if not scenarios:
            raise ConflictError("Cannot run an empty test suite")
        budget = self._budget(payload.budget or {}, len(scenarios))
        snapshot = self._snapshot(agent, version, suite, list(scenarios), budget)
        run = Run(
            project_id=project.id,
            test_suite_id=suite.id,
            agent_version_id=version.id,
            snapshot=snapshot,
            total_cases=len(scenarios),
        )
        self.session.add(run)
        await self.session.flush()
        add_audit(
            self.session,
            organization_id=project.organization_id,
            user_id=self.user.id,
            project_id=project.id,
            action="run.create",
            resource_type="run",
            resource_id=run.id,
            metadata={"suite_id": str(suite.id), "agent_version_id": str(version.id)},
        )
        await self.session.commit()
        try:
            await self.enqueuer.enqueue(run.id)
        except ConflictError:
            run.status = RunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            await self.session.commit()
            raise
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            await self.session.commit()
            raise QueueUnavailableError("Evaluation queue is temporarily unavailable") from exc
        return run

    def _budget(self, requested: dict[str, int], scenario_count: int) -> dict[str, int]:
        budget = {
            "max_cases": self.settings.max_cases_per_run,
            "per_case_timeout_seconds": self.settings.default_case_timeout_seconds,
            "max_tool_calls_per_case": self.settings.max_tool_calls_per_case,
            "max_total_tokens": self.settings.max_total_tokens,
        }
        allowed_max = dict(budget)
        for key, value in requested.items():
            if key not in budget or value < 1 or value > allowed_max[key]:
                raise ConflictError(f"Invalid or excessive run budget: {key}")
            budget[key] = value
        if scenario_count > budget["max_cases"]:
            raise ConflictError("Suite exceeds the configured maximum cases per run")
        return budget

    @staticmethod
    def _snapshot(
        agent: Agent,
        version: AgentVersion,
        suite: TestSuite,
        scenarios: list[Scenario],
        budget: dict[str, int],
    ) -> dict[str, Any]:
        return {
            "agent": {
                "id": str(agent.id),
                "version_id": str(version.id),
                "version": version.version,
                "adapter_type": version.adapter_type,
                "endpoint_url": version.endpoint_url,
                "model_provider": version.model_provider,
                "model_name": version.model_name,
                "system_prompt_hash": hashlib.sha256(version.system_prompt.encode()).hexdigest(),
                "config": deepcopy(version.config),
                "tool_registry": deepcopy(version.tool_registry),
            },
            "suite": {
                "id": str(suite.id),
                "version": suite.version,
                "gate_policy": deepcopy(suite.gate_policy),
            },
            "scenario_ids": [str(scenario.id) for scenario in scenarios],
            "scenarios": [
                {
                    "id": str(scenario.id),
                    "name": scenario.name,
                    "input": scenario.input,
                    "expected_output": scenario.expected_output,
                    "expected_tools": deepcopy(scenario.expected_tools),
                    "forbidden_tools": deepcopy(scenario.forbidden_tools),
                    "expected_tool_arguments": deepcopy(scenario.expected_tool_arguments),
                    "tags": deepcopy(scenario.tags),
                    "severity": scenario.severity.value,
                    "timeout_seconds": scenario.timeout_seconds,
                    "metadata": deepcopy(scenario.scenario_metadata),
                }
                for scenario in scenarios
            ],
            "evaluator_versions": DeterministicEvaluationEngine().versions,
            "budget": budget,
            "created_at": datetime.now(UTC).isoformat(),
        }
