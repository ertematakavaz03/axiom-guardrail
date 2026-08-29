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


class DeterministicEvaluationEngine:
    version = "phase1-1.0"

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

    @property
    def versions(self) -> dict[str, str]:
        return {evaluator.name: evaluator.version for evaluator in self.evaluators}

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        return [result for evaluator in self.evaluators for result in evaluator.evaluate(context)]
