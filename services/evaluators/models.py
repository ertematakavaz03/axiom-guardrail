from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from packages.agent_sdk.contracts import AgentExecutionResult
from services.rag.models import GoldEvidenceRef, RetrievalResult, RetrievalScope
from services.tool_gateway.gateway import GatewayRecord


class EvaluationResult(BaseModel):
    metric: str
    value: float | None = None
    passed: bool
    reason_code: str | None = None
    explanation: str
    expected: Any = None
    actual: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class EvaluationContext(BaseModel):
    scenario: dict[str, Any]
    execution: AgentExecutionResult | None = None
    gateway_records: list[GatewayRecord] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)
    agent_config: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, int] = Field(default_factory=dict)
    retrieval: RetrievalResult | None = None
    gold_evidence: list[GoldEvidenceRef] = Field(default_factory=list)
    rag_scope: RetrievalScope | None = None


class Evaluator(Protocol):
    name: str
    version: str

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]: ...


class SemanticJudge(Protocol):
    """Phase 2 extension point; no implementation or external model is required in Phase 1."""

    async def evaluate_semantics(self, context: EvaluationContext) -> EvaluationResult: ...
