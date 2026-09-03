from __future__ import annotations

import uuid
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    result: Any | None = None


class AgentCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citation_id: str
    chunk_id: str
    document_id: str
    claim_ids: list[str] = Field(default_factory=list)


class AgentClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    type: Literal["factual", "non_factual"] = "factual"


class AgentExecutionRequest(BaseModel):
    run_id: uuid.UUID
    case_id: uuid.UUID
    scenario: dict[str, Any]
    agent_config: dict[str, Any]
    sandbox_context: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, str]] = Field(default_factory=list)


class AgentExecutionResult(BaseModel):
    final_response: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    citations: list[AgentCitation] = Field(default_factory=list)
    claims: list[AgentClaim] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(ge=0)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAdapter(Protocol):
    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult: ...
