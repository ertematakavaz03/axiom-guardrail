from __future__ import annotations

from typing import Any


class AgentArenaError(Exception):
    reason_code = "AGENTARENA_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AgentConnectionError(AgentArenaError):
    reason_code = "AGENT_CONNECTION_ERROR"


class AgentTimeoutError(AgentArenaError):
    reason_code = "AGENT_TIMEOUT"


class ToolTimeoutError(AgentArenaError):
    reason_code = "TOOL_TIMEOUT"


class AgentResponseValidationError(AgentArenaError):
    reason_code = "AGENT_RESPONSE_VALIDATION_ERROR"


class ToolValidationError(AgentArenaError):
    reason_code = "INVALID_TOOL_ARGUMENT_SCHEMA"


class ToolAuthorizationError(AgentArenaError):
    reason_code = "TOOL_POLICY_VIOLATION"


class EvaluationError(AgentArenaError):
    reason_code = "EVALUATION_ERROR"


class ResourceNotFoundError(AgentArenaError):
    reason_code = "RESOURCE_NOT_FOUND"


class ConflictError(AgentArenaError):
    reason_code = "CONFLICT"


class QueueUnavailableError(AgentArenaError):
    reason_code = "QUEUE_UNAVAILABLE"
