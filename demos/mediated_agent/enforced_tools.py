"""The one node that differs from the pinned upstream graph.

Upstream runs ``ToolNode(tools)``: whatever the model asked for is executed. This node
runs the same tools, but only after :class:`~services.security.runtime.RuntimeEnforcer`
authorizes the call against trusted state. A refused call returns a tool message with
``status="error"`` and never reaches the executor, so the side effect does not happen and
the trace records that it did not.

Two properties are worth stating because they are what the whole phase rests on:

* **The model's proposal is data.** Arguments are validated and resource-bound before
  execution; text in a message, a retrieved document or a prior tool result has no path
  to the authorization decision. A conversation that convinces the model it is an admin
  changes the model's behaviour and changes nothing here.
* **The refusal is honest.** A denied call is reported to the model as refused, with its
  policy reason, so the agent can tell the customer the truth instead of hallucinating a
  success. The refusal text carries no policy internals the model could route around and
  no content from the system prompt.

Transport is injected. Everything except the LangChain message construction is a plain
function over plain data, so the enforcement path is unit-testable without LangGraph,
LangChain or a model.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import Any

from demos.mediated_agent.policy import (
    LATEST_CUSTOMER_MESSAGE,
    runtime_policy,
    security_policy,
)
from services.security.runtime import (
    RuntimeDecision,
    RuntimeEnforcer,
    ToolProposal,
    TrustedContext,
)

#: What a refused call reports back to the model. Deliberately plain: it states that the
#: action did not happen and why, in policy terms, without naming a bypass.
REFUSAL_TEMPLATE = (
    "This action was not performed. The request was refused by the deployment's "
    "authorization policy ({decision}: {reasons}). Tell the customer plainly that you "
    "cannot complete it, and do not retry it in another form."
)

#: What an executor failure reports. A failure is evidence, never a defence.
ERROR_TEMPLATE = "This action failed to execute ({error}). No result is available."


def _executable_arguments(tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Narrow bound arguments to the parameters the upstream tool actually declares.

    Server-bound fields exist for the authorization decision and may have no counterpart
    in a single-tenant upstream signature. Passing one through would raise a TypeError
    inside the tool and turn a policy success into a spurious runtime failure.
    """
    schema = getattr(tool, "args", None)
    if isinstance(schema, dict) and schema:
        allowed = set(schema)
    else:  # pragma: no cover - only for tools without a LangChain args schema
        func = getattr(tool, "func", tool)
        try:
            allowed = set(inspect.signature(func).parameters)
        except (TypeError, ValueError):
            return dict(arguments)
    return {key: value for key, value in arguments.items() if key in allowed}


def _default_message_factory(
    *, content: str, name: str, tool_call_id: str, status: str
) -> Any:  # pragma: no cover - exercised only where LangChain is installed
    from langchain_core.messages import ToolMessage

    return ToolMessage(content=content, name=name, tool_call_id=tool_call_id, status=status)


class EnforcedToolNode:
    """A drop-in replacement for ``ToolNode`` that authorizes before it executes.

    One :class:`RuntimeEnforcer` is held per conversation thread, because the controls
    that need memory — the step budget and duplicate-write suppression — are properties of
    a conversation, not of a single graph invocation.
    """

    def __init__(
        self,
        tools: Sequence[Any],
        *,
        context_factory: Callable[[str], TrustedContext],
        message_factory: Callable[..., Any] = _default_message_factory,
        require_external_approval: bool = False,
    ) -> None:
        self.tools = {getattr(tool, "name", ""): tool for tool in tools}
        self.context_factory = context_factory
        self.require_external_approval = require_external_approval
        self.message_factory = message_factory
        self._enforcers: dict[str, RuntimeEnforcer] = {}
        #: Every decision taken, in order, for the run evidence.
        self.decisions: list[RuntimeDecision] = []

    # -- enforcer lifetime ---------------------------------------------------------
    def enforcer_for(self, thread_id: str) -> RuntimeEnforcer:
        existing = self._enforcers.get(thread_id)
        if existing is None:
            existing = RuntimeEnforcer(
                security_policy(),
                self.context_factory(thread_id),
                runtime=runtime_policy(
                    require_approval_for_external=self.require_external_approval
                ),
            )
            self._enforcers[thread_id] = existing
        return existing

    # -- the node ------------------------------------------------------------------
    def __call__(self, state: Any, config: Any = None) -> dict[str, Any]:
        messages = state["messages"] if isinstance(state, dict) else state.messages
        last = messages[-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        thread_id = _thread_id(config)

        enforcer = self.enforcer_for(thread_id)
        # Publish the customer's own most recent turn as host state. This is what the
        # upstream tool documents ``customer_message`` to be, and binding it there is what
        # stops an influenced model from choosing the text that leaves the system.
        latest = _latest_user_message(messages)
        if latest is not None:
            enforcer.update_payload_values({LATEST_CUSTOMER_MESSAGE: latest})
        out: list[Any] = []
        for call in calls:
            out.append(self._handle(enforcer, call))
        return {"messages": out}

    def _handle(self, enforcer: RuntimeEnforcer, call: dict[str, Any]) -> Any:
        arguments = call.get("args")
        proposal = ToolProposal(
            call_id=str(call.get("id") or "call"),
            tool=str(call.get("name") or "unknown"),
            arguments=arguments if isinstance(arguments, dict) else {},
        )
        decision = enforcer.authorize(proposal)
        self.decisions.append(decision)

        if not decision.permits_execution:
            return self.message_factory(
                content=REFUSAL_TEMPLATE.format(
                    decision=decision.decision, reasons=", ".join(decision.reasons) or "policy"
                ),
                name=proposal.tool,
                tool_call_id=proposal.call_id,
                status="error",
            )

        tool = self.tools.get(decision.tool)
        if tool is None:  # pragma: no cover - the enforcer denies unknown tools first
            return self.message_factory(
                content=ERROR_TEMPLATE.format(error="tool not registered"),
                name=proposal.tool,
                tool_call_id=proposal.call_id,
                status="error",
            )

        try:
            result = tool.invoke(_executable_arguments(tool, decision.bound_arguments))
        except Exception as exc:  # noqa: BLE001 - surfaced as evidence, never as safety
            return self.message_factory(
                content=ERROR_TEMPLATE.format(error=type(exc).__name__),
                name=proposal.tool,
                tool_call_id=proposal.call_id,
                status="error",
            )

        # Recorded only after the call actually completed, so a retry of a write that
        # failed is still allowed while a retry of one that succeeded is suppressed.
        enforcer.record_execution(decision)
        return self.message_factory(
            content=result if isinstance(result, str) else str(result),
            name=proposal.tool,
            tool_call_id=proposal.call_id,
            status="success",
        )

    # -- evidence ------------------------------------------------------------------
    def evidence(self) -> dict[str, Any]:
        """Per-thread enforcement evidence. Carries reason codes, never argument values."""
        return {
            "threads": {
                thread: enforcer.evidence() for thread, enforcer in sorted(self._enforcers.items())
            }
        }


def _latest_user_message(messages: Sequence[Any]) -> str | None:
    """The most recent human turn, read from the graph state the host owns.

    Read back-to-front so a multi-turn conversation forwards the turn that actually
    triggered the escalation rather than the first thing the customer ever said.
    """
    for message in reversed(list(messages)):
        kind = getattr(message, "type", None)
        if kind is None and isinstance(message, dict):
            kind = message.get("type") or message.get("role")
        if kind in ("human", "user"):
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")
            if isinstance(content, str) and content:
                return content
    return None


def _thread_id(config: Any) -> str:
    """Pull the thread id out of a RunnableConfig, falling back to a single bucket.

    The fallback is deliberately one shared bucket rather than a fresh enforcer per call:
    losing the thread id must not silently reset a step budget or a write-suppression
    record, which would weaken enforcement exactly when the plumbing is misbehaving.
    """
    if isinstance(config, dict):
        configurable = config.get("configurable")
        if isinstance(configurable, dict):
            value = configurable.get("thread_id")
            if value:
                return str(value)
    return "default"
