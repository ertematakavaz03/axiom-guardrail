from __future__ import annotations

import time
import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.api.app.config import Settings
from apps.api.app.errors import (
    AgentConnectionError,
    AgentResponseValidationError,
    AgentTimeoutError,
)
from packages.agent_sdk.contracts import (
    AgentAdapter,
    AgentCitation,
    AgentClaim,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentToolCall,
    TokenUsage,
)


class ExternalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["assistant", "tool", "user", "system"]
    content: str


class ExternalUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ExternalToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    result: Any | None = None


class ExternalAgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    final_response: str
    messages: list[ExternalMessage] = Field(default_factory=list)
    tool_calls: list[ExternalToolCall] = Field(default_factory=list)
    citations: list[AgentCitation] = Field(default_factory=list)
    claims: list[AgentClaim] = Field(default_factory=list)
    usage: ExternalUsage = Field(default_factory=ExternalUsage)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenericHttpAgentAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        endpoint = request.agent_config.get("endpoint_url")
        if not endpoint:
            raise AgentConnectionError("Generic HTTP agent has no endpoint URL")
        timeout = float(request.scenario.get("timeout_seconds", 30))
        headers = {"Content-Type": "application/json"}
        if self.settings.generic_http_secret:
            headers["Authorization"] = (
                f"Bearer {self.settings.generic_http_secret.get_secret_value()}"
            )
        payload = {
            "messages": request.messages,
            "context": {
                "run_id": str(request.run_id),
                "case_id": str(request.case_id),
                "sandbox": True,
            },
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(str(endpoint), json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AgentTimeoutError(
                "Agent endpoint timed out", details={"transient": True}
            ) from exc
        except httpx.NetworkError as exc:
            raise AgentConnectionError(
                "Agent endpoint could not be reached", details={"transient": True}
            ) from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise AgentConnectionError(
                f"Agent endpoint returned HTTP {response.status_code}",
                details={"transient": True, "status_code": response.status_code},
            )
        if response.is_error:
            raise AgentConnectionError(
                f"Agent endpoint returned HTTP {response.status_code}",
                details={"transient": False, "status_code": response.status_code},
            )
        try:
            parsed = ExternalAgentResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AgentResponseValidationError(
                "Agent response did not match the Axiom Guardrail HTTP contract",
                details={"transient": False},
            ) from exc
        return AgentExecutionResult(
            final_response=parsed.final_response,
            messages=[message.model_dump() for message in parsed.messages],
            tool_calls=[
                AgentToolCall.model_validate(call.model_dump()) for call in parsed.tool_calls
            ],
            citations=parsed.citations,
            claims=parsed.claims,
            token_usage=TokenUsage.model_validate(parsed.usage.model_dump()),
            latency_ms=latency_ms,
            raw_metadata=parsed.metadata,
        )


class DemoSupportAgentAdapter:
    """Deterministic agent used to demonstrate tool and policy evidence."""

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        text = request.scenario["input"].lower()
        metadata = request.scenario.get("metadata", {})
        calls: list[AgentToolCall] = []
        response = "I need more information to help with that request."

        def call(name: str, arguments: dict[str, Any]) -> None:
            calls.append(
                AgentToolCall(
                    tool_call_id=f"demo-{uuid.uuid4().hex[:10]}", name=name, arguments=arguments
                )
            )

        if metadata.get("demo_behavior") == "no_tool":
            response = "I could not locate that information."
        elif metadata.get("demo_behavior") == "unexpected_tool":
            call(
                "create_ticket",
                {"customer_id": "CUS-1001", "subject": "Unexpected", "description": text},
            )
            response = "I created a ticket."
        elif "refund" in text:
            order_id = _extract(text, "ord-") or "ORD-1001"
            call("get_order", {"order_id": order_id})
            confirmed = any(word in text for word in ("confirmed", "i confirm", "approved"))
            call(
                "refund_order",
                {
                    "order_id": order_id,
                    "amount": 49.99,
                    "reason": "Customer requested refund",
                    "confirmed": confirmed,
                },
            )
            response = (
                "The refund request was processed." if confirmed else "I attempted the refund."
            )
        elif "ticket" in text:
            call(
                "create_ticket",
                {
                    "customer_id": _extract(text, "cus-") or "CUS-1001",
                    "subject": "Support request",
                    "description": request.scenario["input"],
                },
            )
            response = "A support ticket was created."
        elif "customer" in text or "email" in text:
            email = _extract_email(text)
            arguments = (
                {"email": email} if email else {"customer_id": _extract(text, "cus-") or "CUS-404"}
            )
            call("search_customer", arguments)
            response = "I searched for the customer."
        elif "order" in text:
            lookup_order_id = _extract(text, "ord-")
            arguments = {"order_id": lookup_order_id} if lookup_order_id else {}
            call("get_order", arguments)
            response = "I looked up the order."

        input_tokens = max(1, len(text.split()) * 2)
        output_tokens = max(1, len(response.split()) * 2)
        return AgentExecutionResult(
            final_response=response,
            messages=[{"role": "assistant", "content": response}],
            tool_calls=calls,
            token_usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            latency_ms=25 + 10 * len(calls),
            raw_metadata={"adapter": "deterministic-demo"},
        )


class DemoRagAgentAdapter:
    """Deterministic evidence-aware agent used by the Phase 2 benchmark."""

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        evidence = list(request.sandbox_context.get("retrieved_evidence", []))
        metadata = request.scenario.get("metadata", {})
        behavior = metadata.get("demo_behavior", "grounded")
        citations: list[AgentCitation] = []
        claims: list[AgentClaim] = []
        if not evidence:
            response = "I could not find sufficient evidence to answer the question."
        else:
            selected = evidence[0]
            if behavior == "wrong_citation":
                gold_chunk_ids = {
                    str(item.get("chunk_id"))
                    for item in request.scenario.get("gold_evidence", [])
                    if item.get("chunk_id")
                }
                selected = next(
                    (item for item in evidence if str(item.get("chunk_id")) not in gold_chunk_ids),
                    evidence[0],
                )
            if behavior in {"unsupported_claim", "hallucinated_value"}:
                response = str(
                    metadata.get(
                        "demo_answer",
                        "Customers can request refunds within 30 days.",
                    )
                )
            elif behavior == "partial_grounding":
                response = f"{evidence[0]['content']} Processing always takes 90 days."
            else:
                response = str(metadata.get("demo_answer", evidence[0]["content"]))
            sentence_parts = [
                part.strip()
                for part in response.replace("!", ".").replace("?", ".").split(".")
                if part.strip()
            ]
            claims = [
                AgentClaim(id=f"claim_{index}", text=f"{sentence}.", type="factual")
                for index, sentence in enumerate(sentence_parts, start=1)
            ]
            if behavior != "missing_citation" and claims:
                if behavior == "citation_not_retrieved":
                    chunk_id = str(uuid.UUID(int=0))
                    document_id = str(uuid.UUID(int=0))
                else:
                    chunk_id = str(selected["chunk_id"])
                    document_id = str(selected["document_id"])
                citations.append(
                    AgentCitation(
                        citation_id="c1",
                        chunk_id=chunk_id,
                        document_id=document_id,
                        claim_ids=[claim.id for claim in claims],
                    )
                )
        input_tokens = max(1, len(request.scenario["input"].split()) * 2)
        output_tokens = max(1, len(response.split()) * 2)
        return AgentExecutionResult(
            final_response=response,
            messages=[{"role": "assistant", "content": response}],
            citations=citations,
            claims=claims,
            token_usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            latency_ms=35,
            raw_metadata={"adapter": "deterministic-rag-demo", "evidence_count": len(evidence)},
        )


def adapter_for(agent_config: dict[str, Any], settings: Settings) -> AgentAdapter:
    adapter_type = agent_config.get("adapter_type")
    if adapter_type == "generic_http":
        return GenericHttpAgentAdapter(settings)
    if adapter_type == "demo_support_agent":
        return DemoSupportAgentAdapter()
    if adapter_type == "demo_rag_agent":
        return DemoRagAgentAdapter()
    raise AgentResponseValidationError(f"Unsupported adapter type: {adapter_type}")


def _extract(text: str, prefix: str) -> str | None:
    for word in text.replace(",", " ").replace(".", " ").split():
        cleaned = word.strip(".?!:;()[]{}\"'")
        if cleaned.startswith(prefix):
            return cleaned.upper()
    return None


def _extract_email(text: str) -> str | None:
    for word in text.replace(",", " ").split():
        if "@" in word:
            return word.strip(".?!")
    return None
