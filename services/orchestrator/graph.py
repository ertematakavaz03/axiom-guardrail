from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from apps.api.app.config import Settings
from apps.api.app.errors import AgentArenaError, AgentTimeoutError
from apps.api.app.security.redaction import redact_secrets
from packages.agent_sdk.contracts import AgentExecutionRequest, AgentExecutionResult
from services.evaluators.engine import DeterministicEvaluationEngine
from services.evaluators.models import EvaluationContext, EvaluationResult
from services.evaluators.verdicts import decide_case_verdict
from services.orchestrator.adapters import adapter_for
from services.rag.embeddings import embedding_provider_for
from services.rag.errors import QdrantTimeoutError
from services.rag.models import GoldEvidenceRef, RetrievalResult, RetrievalScope
from services.rag.reranking import reranker_for
from services.rag.retrieval import RetrievalService
from services.rag.storage import QdrantVectorStore
from services.tool_gateway.gateway import GatewayRecord, ToolGateway


class TraceEvent(BaseModel):
    sequence_number: int
    event_type: str
    name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OrchestrationResult(BaseModel):
    final_response: str
    traces: list[TraceEvent]
    evaluations: list[EvaluationResult]
    verdict: Literal["pass", "warn", "block"]
    score: float
    reason_codes: list[str]
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float = 0.0


class CaseState(TypedDict, total=False):
    run_id: str
    case_id: str
    scenario: dict[str, Any]
    agent_config: dict[str, Any]
    suite_config: dict[str, Any]
    messages: list[dict[str, str]]
    traces: list[TraceEvent]
    execution: AgentExecutionResult | None
    gateway_records: list[GatewayRecord]
    evaluations: list[EvaluationResult]
    errors: list[dict[str, Any]]
    retry_count: int
    retry_requested: bool
    budget: dict[str, int]
    verdict: Literal["pass", "warn", "block"]
    score: float
    reason_codes: list[str]
    rag_config: dict[str, Any] | None
    retrieval: RetrievalResult | None
    rag_scope: RetrievalScope | None


def is_transient_failure(error: AgentArenaError) -> bool:
    return bool(error.details.get("transient"))


class CaseOrchestrator:
    max_retries = 2

    def __init__(
        self, settings: Settings, engine: DeterministicEvaluationEngine | None = None
    ) -> None:
        self.settings = settings
        self.engine = engine or DeterministicEvaluationEngine()
        graph = StateGraph(CaseState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("prepare_scenario", self._prepare_scenario)
        graph.add_node("retrieve_evidence", self._retrieve_evidence)
        graph.add_node("execute_agent", self._execute_agent)
        graph.add_node("repair_or_retry", self._repair_or_retry)
        graph.add_node("collect_trace", self._collect_trace)
        graph.add_node("evaluate_deterministic", self._evaluate)
        graph.add_node("finalize", self._finalize)
        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "prepare_scenario")
        graph.add_conditional_edges(
            "prepare_scenario",
            self._route_after_prepare,
            {"rag": "retrieve_evidence", "standard": "execute_agent"},
        )
        graph.add_edge("retrieve_evidence", "execute_agent")
        graph.add_conditional_edges(
            "execute_agent",
            self._route_after_execution,
            {"retry": "repair_or_retry", "continue": "collect_trace"},
        )
        graph.add_edge("repair_or_retry", "execute_agent")
        graph.add_edge("collect_trace", "evaluate_deterministic")
        graph.add_edge("evaluate_deterministic", "finalize")
        graph.add_edge("finalize", END)
        self.graph = graph.compile()

    async def run_case(
        self,
        *,
        run_id: str,
        case_id: str,
        scenario: dict[str, Any],
        agent_config: dict[str, Any],
        suite_config: dict[str, Any],
        budget: dict[str, int],
        rag_config: dict[str, Any] | None = None,
    ) -> OrchestrationResult:
        state: CaseState = {
            "run_id": run_id,
            "case_id": case_id,
            "scenario": scenario,
            "agent_config": agent_config,
            "suite_config": suite_config,
            "messages": [],
            "traces": [],
            "execution": None,
            "gateway_records": [],
            "evaluations": [],
            "errors": [],
            "retry_count": 0,
            "retry_requested": False,
            "budget": budget,
            "rag_config": rag_config,
            "retrieval": None,
            "rag_scope": None,
        }
        final = await self.graph.ainvoke(state)
        execution = final.get("execution")
        cost_result = next(
            (item for item in final["evaluations"] if item.metric == "estimated_cost"), None
        )
        return OrchestrationResult(
            final_response=execution.final_response if execution else "",
            traces=final["traces"],
            evaluations=final["evaluations"],
            verdict=final["verdict"],
            score=final["score"],
            reason_codes=final["reason_codes"],
            latency_ms=(execution.latency_ms if execution else 0)
            + sum(record.duration_ms for record in final["gateway_records"])
            + (final["retrieval"].timings.total_ms if final.get("retrieval") else 0),
            input_tokens=execution.token_usage.input_tokens if execution else None,
            output_tokens=execution.token_usage.output_tokens if execution else None,
            total_tokens=execution.token_usage.total_tokens if execution else None,
            estimated_cost=cost_result.value if cost_result and cost_result.value else 0.0,
        )

    async def _load_context(self, state: CaseState) -> dict[str, Any]:
        traces = list(state["traces"])
        self._trace(
            traces,
            "agent_input",
            "user",
            {"input": state["scenario"]["input"], "sandbox": True},
        )
        return {"traces": traces}

    async def _prepare_scenario(self, state: CaseState) -> dict[str, Any]:
        return {"messages": [{"role": "user", "content": state["scenario"]["input"]}]}

    def _route_after_prepare(self, state: CaseState) -> Literal["rag", "standard"]:
        enabled = bool(state["scenario"].get("metadata", {}).get("rag_enabled"))
        return "rag" if enabled and state.get("rag_config") else "standard"

    async def _retrieve_evidence(self, state: CaseState) -> dict[str, Any]:
        traces = list(state["traces"])
        errors = list(state["errors"])
        config = state.get("rag_config") or {}
        scope = RetrievalScope(
            organization_id=str(config["organization_id"]),
            project_id=str(config["project_id"]),
            corpus_id=str(config["corpus_id"]),
        )
        query = str(state["scenario"]["input"])
        metadata = state["scenario"].get("metadata", {})
        self._trace(
            traces,
            "retrieval_query",
            "hybrid",
            {
                "query": query,
                "filters": metadata.get("retrieval_filters", {}),
                "scope": scope.model_dump(),
            },
        )
        self._trace(traces, "reranking_started", str(config.get("reranker_type")), {})
        parameters = config.get("retrieval_parameters", {})
        store: QdrantVectorStore | None = None
        try:
            store = QdrantVectorStore(
                self.settings.qdrant_url,
                self.settings.qdrant_collection_prefix,
                self.settings.embedding_vector_size,
                self.settings.rag_retrieval_timeout_seconds,
            )
            service = RetrievalService(
                store,
                embedding_provider_for(
                    self.settings,
                    str(config.get("embedding_provider", self.settings.embedding_provider)),
                    str(config.get("embedding_model", self.settings.embedding_model)),
                ),
                reranker_for(str(config.get("reranker_type", "token_overlap"))),
                self.settings.rag_retrieval_timeout_seconds,
            )
            if metadata.get("demo_behavior") == "retrieval_timeout":
                raise QdrantTimeoutError(
                    "Deterministic retrieval timeout", details={"transient": True}
                )
            retrieval = await service.retrieve(
                query=query,
                scope=scope,
                filters=metadata.get("retrieval_filters", {}),
                dense_enabled=bool(config.get("dense_enabled", True)),
                sparse_enabled=bool(config.get("sparse_enabled", True)),
                top_k_dense=int(parameters.get("top_k_dense", 20)),
                top_k_sparse=int(parameters.get("top_k_sparse", 20)),
                hybrid_top_k=int(parameters.get("hybrid_top_k", 10)),
                rerank_top_n=int(parameters.get("rerank_top_n", 5)),
            )
        except AgentArenaError as error:
            safe_error = {
                "reason_code": error.reason_code,
                "explanation": error.message,
                "details": redact_secrets(error.details),
            }
            errors.append(safe_error)
            self._trace(traces, "retrieval_failed", "hybrid", safe_error)
            return {"retrieval": None, "rag_scope": scope, "errors": errors, "traces": traces}
        finally:
            if store is not None:
                await store.close()
        self._trace(
            traces,
            "dense_retrieval_results",
            "dense",
            {"hits": [hit.model_dump() for hit in retrieval.dense_hits]},
            retrieval.timings.dense_ms,
        )
        self._trace(
            traces,
            "sparse_retrieval_results",
            "sparse",
            {"hits": [hit.model_dump() for hit in retrieval.sparse_hits]},
            retrieval.timings.sparse_ms,
        )
        self._trace(
            traces,
            "hybrid_retrieval_results",
            "rrf",
            {
                "candidate_count": retrieval.candidate_count,
                "hits": [hit.model_dump() for hit in retrieval.hits],
            },
            retrieval.timings.fusion_ms,
        )
        self._trace(
            traces,
            "reranking_completed",
            str(config.get("reranker_type")),
            {"candidate_count": retrieval.candidate_count, "final_count": len(retrieval.hits)},
            retrieval.timings.reranking_ms,
        )
        self._trace(
            traces,
            "retrieval_evidence_selected",
            "top_evidence",
            {"hits": [hit.model_dump() for hit in retrieval.hits]},
        )
        evidence_context = [
            {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "document_name": hit.document_name,
                "content": hit.content,
                "metadata": hit.metadata,
            }
            for hit in retrieval.hits
        ]
        messages = [
            *state["messages"],
            {"role": "system", "content": f"Retrieved evidence: {evidence_context}"},
        ]
        self._trace(
            traces,
            "agent_context_created",
            "retrieved_evidence",
            {
                "evidence_count": len(evidence_context),
                "chunk_ids": [hit.chunk_id for hit in retrieval.hits],
            },
        )
        return {
            "retrieval": retrieval,
            "rag_scope": scope,
            "messages": messages,
            "traces": traces,
        }

    async def _execute_agent(self, state: CaseState) -> dict[str, Any]:
        traces = list(state["traces"])
        errors = list(state["errors"])
        adapter = adapter_for(state["agent_config"], self.settings)
        retrieval = state.get("retrieval")
        request = AgentExecutionRequest(
            run_id=state["run_id"],
            case_id=state["case_id"],
            scenario=state["scenario"],
            agent_config=state["agent_config"],
            messages=state["messages"],
            sandbox_context={
                "enabled": True,
                "retrieved_evidence": [hit.model_dump() for hit in retrieval.hits]
                if retrieval
                else [],
            },
        )
        error: AgentArenaError
        try:
            timeout_seconds = min(
                int(state["scenario"].get("timeout_seconds", 30)),
                int(state["budget"].get("per_case_timeout_seconds", 30)),
            )
            async with asyncio.timeout(timeout_seconds):
                execution = await adapter.execute(request)
            self._trace(
                traces,
                "agent_output",
                "assistant",
                {
                    "final_response": execution.final_response,
                    "usage": execution.token_usage.model_dump(),
                    "metadata": execution.raw_metadata,
                },
                execution.latency_ms,
            )
            for call in execution.tool_calls:
                self._trace(
                    traces,
                    "tool_requested",
                    call.name,
                    {"tool_call_id": call.tool_call_id, "arguments": call.arguments},
                )
            for citation in execution.citations:
                self._trace(
                    traces,
                    "citation_emitted",
                    citation.citation_id,
                    citation.model_dump(),
                )
            for claim in execution.claims:
                self._trace(traces, "claim_extracted", claim.id, claim.model_dump())
            return {"execution": execution, "traces": traces, "retry_requested": False}
        except TimeoutError:
            error = AgentTimeoutError(
                "Case execution exceeded its timeout", details={"transient": True}
            )
        except AgentArenaError as caught:
            error = caught
        safe_error = {
            "reason_code": error.reason_code,
            "explanation": error.message,
            "details": redact_secrets(error.details),
        }
        errors.append(safe_error)
        retry = is_transient_failure(error) and state["retry_count"] < self.max_retries
        if not retry:
            self._trace(traces, "error", None, safe_error)
        return {"errors": errors, "traces": traces, "retry_requested": retry, "execution": None}

    def _route_after_execution(self, state: CaseState) -> Literal["retry", "continue"]:
        return "retry" if state.get("retry_requested") else "continue"

    async def _repair_or_retry(self, state: CaseState) -> dict[str, Any]:
        traces = list(state["traces"])
        retry_count = state["retry_count"] + 1
        self._trace(
            traces,
            "retry",
            "agent_execution",
            {
                "attempt": retry_count + 1,
                "reason_code": state["errors"][-1]["reason_code"],
                "classification": "transient_infrastructure",
            },
        )
        return {"retry_count": retry_count, "traces": traces, "retry_requested": False}

    async def _collect_trace(self, state: CaseState) -> dict[str, Any]:
        traces = list(state["traces"])
        execution = state.get("execution")
        gateway = ToolGateway(
            registry=state["agent_config"].get("tool_registry"),
            forbidden_tools=state["scenario"].get("forbidden_tools", []),
            max_tool_calls=int(state["budget"].get("max_tool_calls_per_case", 10)),
            sandbox_mode=True,
            timeout_tools=(
                state["scenario"].get("metadata", {}).get("timeout_tools")
                or (
                    state["scenario"].get("expected_tools", [])
                    if state["scenario"].get("metadata", {}).get("demo_behavior") == "timeout"
                    else []
                )
            ),
            simulated_timeout_ms=int(state["scenario"].get("timeout_seconds", 30)) * 1000,
        )
        records = await gateway.process(execution.tool_calls if execution else [])
        for record in records:
            self._trace(
                traces,
                "tool_policy_decision",
                record.name,
                {
                    "tool_call_id": record.tool_call_id,
                    "decision": "allow" if record.allowed else "deny",
                    "reason_code": None if record.allowed else record.reason_code,
                    "explanation": (
                        "Tool call passed policy and schema validation"
                        if record.allowed
                        else record.explanation
                    ),
                },
            )
            if record.allowed:
                self._trace(
                    traces,
                    "tool_started",
                    record.name,
                    {"tool_call_id": record.tool_call_id, "arguments": record.arguments},
                )
                if record.execution_failed:
                    self._trace(
                        traces,
                        "tool_failed",
                        record.name,
                        {
                            "tool_call_id": record.tool_call_id,
                            "reason_code": record.reason_code,
                            "explanation": record.explanation,
                        },
                        record.duration_ms,
                    )
                else:
                    self._trace(
                        traces,
                        "tool_completed",
                        record.name,
                        {"tool_call_id": record.tool_call_id, "result": record.result},
                        record.duration_ms,
                    )
        return {"gateway_records": records, "traces": traces}

    async def _evaluate(self, state: CaseState) -> dict[str, Any]:
        evaluations = self.engine.evaluate(
            EvaluationContext(
                scenario=state["scenario"],
                execution=state.get("execution"),
                gateway_records=state["gateway_records"],
                errors=state["errors"],
                pricing=self.settings.model_pricing_json,
                agent_config=state["agent_config"],
                budget=state["budget"],
                retrieval=state.get("retrieval"),
                gold_evidence=[
                    GoldEvidenceRef.model_validate(item)
                    for item in state["scenario"].get("gold_evidence", [])
                ],
                rag_scope=state.get("rag_scope"),
            )
        )
        traces = list(state["traces"])
        self._trace(
            traces,
            "evaluation",
            "deterministic_engine",
            {
                "results": [
                    {
                        "metric": result.metric,
                        "passed": result.passed,
                        "reason_code": result.reason_code,
                    }
                    for result in evaluations
                ]
            },
        )
        if state["scenario"].get("metadata", {}).get("rag_enabled"):
            self._trace(
                traces,
                "citation_evaluated",
                "citation_evaluators",
                {
                    "results": [
                        result.model_dump()
                        for result in evaluations
                        if result.metric.startswith("citation_")
                    ]
                },
            )
            self._trace(
                traces,
                "groundedness_evaluated",
                "groundedness",
                {
                    "results": [
                        result.model_dump()
                        for result in evaluations
                        if result.metric in {"groundedness", "unsupported_claim_rate"}
                    ]
                },
            )
        return {"evaluations": evaluations, "traces": traces}

    async def _finalize(self, state: CaseState) -> dict[str, Any]:
        verdict, score, reasons = decide_case_verdict(
            state["evaluations"],
            str(state["scenario"].get("severity", "medium")),
            state["suite_config"].get("gate_policy", {}),
        )
        return {"verdict": verdict, "score": score, "reason_codes": reasons}

    @staticmethod
    def _trace(
        traces: list[TraceEvent],
        event_type: str,
        name: str | None,
        payload: dict[str, Any],
        duration_ms: int | None = None,
    ) -> None:
        traces.append(
            TraceEvent(
                sequence_number=len(traces) + 1,
                event_type=event_type,
                name=name,
                payload=redact_secrets(payload),
                duration_ms=duration_ms,
            )
        )
