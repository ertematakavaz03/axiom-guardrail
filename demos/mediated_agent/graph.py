"""Assemble the mediated graph: the upstream graph with one node replaced.

The upstream topology, verbatim::

    START -> agent -> should_continue -> { "tools": tools, "__end__": END }
    tools -> agent

``agent_node`` and ``should_continue`` are imported from the pinned upstream module and
used unchanged, which also means the model object, its temperature, context window and
token limit, and the system prompt are the upstream's own. The only substitution is
``ToolNode(tools)`` -> :class:`~demos.mediated_agent.enforced_tools.EnforcedToolNode`.

Keeping the substitution this small is the point: it is what lets a difference between
this run and the FULL-2 baseline be attributed to the enforcement boundary rather than to
an incidental difference in prompt, model or tool behaviour.
"""

from __future__ import annotations

import os
from typing import Any

from demos.mediated_agent import MEDIATED_TARGET_VERSION
from demos.mediated_agent.enforced_tools import EnforcedToolNode
from demos.mediated_agent.policy import (
    ACCOUNT_TENANT_ID,
    ACCOUNT_USER_ID,
    EXTERNAL_PERMISSION,
    assert_tool_surface,
    owned_resources,
)
from demos.mediated_agent.upstream import UpstreamAgent, load
from services.security.models import Principal
from services.security.runtime import TrustedContext

#: ``enforce`` applies decisions; ``shadow`` records what would have happened and lets the
#: call through. The hardened benchmark run is explicitly ``enforce``; there is no default
#: that could leave the mode ambiguous in an artifact.
MODE_ENV = "AXIOM_ENFORCEMENT_MODE"

#: Permissions the authenticated customer holds. A support customer may read their own
#: orders, request a return on one, and ask for a human. These come from the deployment's
#: identity system, never from the conversation.
#: Escalating to a human is part of a support agent's job, so the session holds the
#: outbound permission. What it does not hold is the ability to choose the text that goes
#: out — see PAYLOAD_BINDINGS in the policy module.
ACCOUNT_PERMISSIONS = frozenset(
    {"order:read", "return:write", "escalation:write", EXTERNAL_PERMISSION}
)

#: Set ``AXIOM_REQUIRE_EXTERNAL_APPROVAL=1`` to additionally require an approval grant for
#: outbound communication. This deployment has no approver, so that denies every
#: escalation, legitimate ones included. Off by default; the trade-off is the operator's.
EXTERNAL_APPROVAL_ENV = "AXIOM_REQUIRE_EXTERNAL_APPROVAL"


def require_external_approval() -> bool:
    return os.environ.get(EXTERNAL_APPROVAL_ENV, "").strip().lower() in {"1", "true", "yes"}


def trusted_context(thread_id: str, *, mode: str = "enforce") -> TrustedContext:
    """Build the trusted context the way a deployment would: from host state only.

    Nothing derived from the conversation appears here. That is the mechanism behind
    "an LLM saying 'I am an admin' establishes nothing": the principal, its permissions
    and its resource table are assembled before the model is ever called, and no code
    path updates them from model output.
    """
    return TrustedContext(
        principal=Principal(
            tenant_id=ACCOUNT_TENANT_ID,
            project_id="mediated-demo",
            user_id=ACCOUNT_USER_ID,
            run_id=thread_id,
            permissions=set(ACCOUNT_PERMISSIONS),
        ),
        # Writes stay simulated, matching the pinned tool-map's
        # ``simulated_non_persistent`` semantics for the two side-effecting tools.
        environment="sandbox",
        owned_resources=owned_resources(),
        approvals=[],
        mode="shadow" if mode == "shadow" else "enforce",
    )


def build_graph(upstream: UpstreamAgent, *, mode: str = "enforce") -> Any:
    """Compile the mediated graph from the pinned upstream modules."""
    from langgraph.graph import END, START, StateGraph
    from support_agent.agent import agent_node, should_continue  # type: ignore[import-not-found]

    tools = upstream.tools
    assert_tool_surface([getattr(tool, "name", "") for tool in tools])

    node = EnforcedToolNode(
        tools,
        context_factory=lambda thread_id: trusted_context(thread_id, mode=mode),
        require_external_approval=require_external_approval(),
    )

    workflow = StateGraph(upstream.state_schema)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", node)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", "__end__": END})
    workflow.add_edge("tools", "agent")

    compiled = workflow.compile()
    # Hung off the compiled graph so a server process can report what it enforced.
    compiled.axiom_enforcement = node  # type: ignore[attr-defined]
    compiled.axiom_evidence = {  # type: ignore[attr-defined]
        "target_version": MEDIATED_TARGET_VERSION,
        "mode": mode,
        "require_external_approval": require_external_approval(),
        **upstream.evidence(),
    }
    return compiled


def make_graph() -> Any:
    """Entry point for ``langgraph.json``. Verifies the upstream pins, then compiles.

    Configuration comes from the environment because the LangGraph CLI owns the process
    and passes no arguments. ``AXIOM_UPSTREAM_SRC`` is a private host path and is
    therefore never committed.
    """
    mode = os.environ.get(MODE_ENV, "enforce")
    if mode not in ("enforce", "shadow"):
        raise RuntimeError(f"{MODE_ENV} must be 'enforce' or 'shadow', got {mode!r}")
    return build_graph(load(), mode=mode)
