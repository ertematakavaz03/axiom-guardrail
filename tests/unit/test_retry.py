from apps.api.app.errors import AgentConnectionError, AgentResponseValidationError
from services.orchestrator.graph import is_transient_failure


def test_only_transient_infrastructure_errors_retry() -> None:
    assert is_transient_failure(AgentConnectionError("429", details={"transient": True})) is True
    assert is_transient_failure(AgentConnectionError("400", details={"transient": False})) is False
    assert (
        is_transient_failure(AgentResponseValidationError("semantic", details={"transient": False}))
        is False
    )
