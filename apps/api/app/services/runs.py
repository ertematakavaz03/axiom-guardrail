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
from apps.api.app.db.models import (
    Agent,
    AgentVersion,
    GoldEvidence,
    MCPRegistration,
    RagConfig,
    Run,
    RunStatus,
    Scenario,
    SecurityPolicyRecord,
    TestSuite,
    User,
)
from apps.api.app.errors import ConflictError, QueueUnavailableError
from apps.api.app.repositories.scoping import (
    get_agent_version,
    get_corpus,
    get_project,
    get_rag_config,
    get_suite,
)
from apps.api.app.schemas.resources import RunCreate
from apps.api.app.services.audit import add_audit
from services.evaluators.engine import DeterministicEvaluationEngine
from services.rag.storage import collection_name_for


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
        rag_snapshot: dict[str, Any] | None = None
        gold_by_scenario: dict[uuid.UUID, list[GoldEvidence]] = {}
        if payload.corpus_id or payload.rag_config_id:
            if not payload.corpus_id or not payload.rag_config_id:
                raise ConflictError("RAG runs require both corpus_id and rag_config_id")
            corpus = await get_corpus(self.session, self.user.id, payload.corpus_id)
            rag_config = await get_rag_config(self.session, self.user.id, payload.rag_config_id)
            if corpus.project_id != project.id or rag_config.project_id != project.id:
                raise ConflictError("RAG corpus/config must belong to the run project")
            rag_snapshot = self._rag_snapshot(
                project.organization_id,
                project.id,
                corpus.id,
                corpus.version,
                rag_config,
                self.settings.qdrant_collection_prefix,
            )
            evidence = (
                await self.session.scalars(
                    select(GoldEvidence).where(
                        GoldEvidence.scenario_id.in_([scenario.id for scenario in scenarios])
                    )
                )
            ).all()
            for item in evidence:
                gold_by_scenario.setdefault(item.scenario_id, []).append(item)
        snapshot = self._snapshot(
            agent,
            version,
            suite,
            list(scenarios),
            budget,
            rag_snapshot,
            gold_by_scenario,
        )
        security_scenarios = [s for s in scenarios if "security" in s.scenario_metadata]
        if security_scenarios:
            from services.security.models import SecurityPolicy, SecurityScenario

            if len(security_scenarios) != len(scenarios) or payload.security_policy_id is None:
                raise ConflictError(
                    "Security suites require an explicit security policy and cannot mix scenario kinds"
                )
            policy_record = await self.session.get(SecurityPolicyRecord, payload.security_policy_id)
            if (
                policy_record is None
                or policy_record.project_id != project.id
                or policy_record.agent_id not in {None, agent.id}
            ):
                raise ConflictError("Security policy must belong to this project and agent")
            if (
                payload.security_mode == "preventive"
                and version.adapter_type != "demo_security_agent"
            ):
                raise ConflictError(
                    "External adapters are observational; preventive mode requires a host-owned execution gateway"
                )
            for security_scenario in security_scenarios:
                SecurityScenario.model_validate(security_scenario.scenario_metadata["security"])
            SecurityPolicy.model_validate(policy_record.policy)
            registrations = (
                await self.session.scalars(
                    select(MCPRegistration)
                    .where(
                        MCPRegistration.project_id == project.id, MCPRegistration.approved.is_(True)
                    )
                    .order_by(MCPRegistration.created_at)
                )
            ).all()
            snapshot["security"] = {
                "policy_id": str(policy_record.id),
                "policy": deepcopy(policy_record.policy),
                "policy_hash": policy_record.policy_hash,
                "mode": payload.security_mode,
                "principal": {
                    "tenant_id": str(project.organization_id),
                    "project_id": str(project.id),
                    "user_id": str(self.user.id),
                    "run_id": "pending",
                    "permissions": [],
                },
                "mcp_approved": [deepcopy(item.inventory) for item in registrations],
                "evaluator_version": "security-deterministic-1",
            }
        elif (
            payload.security_policy_id is not None or version.adapter_type == "demo_security_agent"
        ):
            raise ConflictError("Security policies and demo targets require security scenarios")
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
        rag_snapshot: dict[str, Any] | None = None,
        gold_by_scenario: dict[uuid.UUID, list[GoldEvidence]] | None = None,
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
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
                    **(
                        {
                            "gold_evidence": [
                                {
                                    "document_id": str(item.document_id)
                                    if item.document_id
                                    else None,
                                    "document_version_id": str(item.document_version_id)
                                    if item.document_version_id
                                    else None,
                                    "chunk_id": str(item.chunk_id) if item.chunk_id else None,
                                    "relevance_score": float(item.relevance_score),
                                    "required": item.required,
                                    "metadata": deepcopy(item.evidence_metadata),
                                }
                                for item in (gold_by_scenario or {}).get(scenario.id, [])
                            ]
                        }
                        if (gold_by_scenario or {}).get(scenario.id)
                        else {}
                    ),
                }
                for scenario in scenarios
            ],
            "evaluator_versions": DeterministicEvaluationEngine().versions,
            "budget": budget,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if rag_snapshot is not None:
            snapshot["rag"] = deepcopy(rag_snapshot)
        return snapshot

    @staticmethod
    def _rag_snapshot(
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        corpus_id: uuid.UUID,
        corpus_version: str,
        config: RagConfig,
        collection_prefix: str,
    ) -> dict[str, Any]:
        return {
            "organization_id": str(organization_id),
            "project_id": str(project_id),
            "corpus_id": str(corpus_id),
            "corpus_version": corpus_version,
            "rag_config_id": str(config.id),
            "embedding_provider": config.embedding_provider,
            "embedding_model": config.embedding_model,
            "dense_enabled": config.dense_enabled,
            "sparse_enabled": config.sparse_enabled,
            "retrieval_parameters": {
                "top_k_dense": config.top_k_dense,
                "top_k_sparse": config.top_k_sparse,
                "hybrid_top_k": config.hybrid_top_k,
                "rerank_top_n": config.rerank_top_n,
                "metadata_filter_policy": deepcopy(config.metadata_filter_policy),
            },
            "reranker_type": config.reranker_type,
            "reranker_model": config.reranker_model,
            "gold_evidence_version": "1",
            "qdrant_collection_name": collection_name_for(collection_prefix, str(project_id)),
        }
