from __future__ import annotations

from services.evaluators.deterministic import (
    AuthorizationEvaluator,
    CostEvaluator,
    LatencyEvaluator,
    TaskSuccessEvaluator,
    TokenUsageEvaluator,
    ToolArgumentsEvaluator,
    ToolSelectionEvaluator,
)
from services.evaluators.models import EvaluationContext, EvaluationResult, Evaluator
from services.rag.evaluators import CitationAndGroundednessEvaluator, RetrievalEvaluator


class DeterministicEvaluationEngine:
    version = "phase2-2.0"

    def __init__(self, evaluators: list[Evaluator] | None = None) -> None:
        self.evaluators = evaluators or [
            ToolSelectionEvaluator(),
            ToolArgumentsEvaluator(),
            AuthorizationEvaluator(),
            TaskSuccessEvaluator(),
            LatencyEvaluator(),
            TokenUsageEvaluator(),
            CostEvaluator(),
        ]
        self.rag_evaluators: list[Evaluator] = [
            RetrievalEvaluator(),
            CitationAndGroundednessEvaluator(),
        ]

    @property
    def versions(self) -> dict[str, str]:
        return {
            evaluator.name: evaluator.version
            for evaluator in [*self.evaluators, *self.rag_evaluators]
        }

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        evaluators = list(self.evaluators)
        if context.scenario.get("metadata", {}).get("rag_enabled"):
            evaluators.extend(self.rag_evaluators)
        return [result for evaluator in evaluators for result in evaluator.evaluate(context)]
