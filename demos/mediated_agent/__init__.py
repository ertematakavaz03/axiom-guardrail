"""The pinned upstream agent, with its executor replaced by an enforced one.

Why this exists
---------------
``security-real-agent-v1`` drives a pinned third-party LangGraph agent that calls its own
tools internally. Axiom can observe that agent's trace but cannot interpose, so every
FULL-2 case carries ``prevention_status="N/A_no_host_owned_executor"``. Re-running the
frozen suite against that same target after Phase 4 would measure nothing: there is no
point at which new enforcement could act.

What this package changes, and only this
----------------------------------------
The upstream graph is::

    START -> agent -> should_continue -> ToolNode(tools) -> agent

The mediated target is the *same* graph, built from the *same* pinned source: same
``ChatOllama`` configuration, same ``SYSTEM_PROMPT``, same eight tool implementations,
same rendered tool output, same LangGraph server. The single substitution is
``ToolNode(tools)`` -> :class:`~demos.mediated_agent.enforced_tools.EnforcedToolNode`,
which turns each tool call into a :class:`~services.security.runtime.ToolProposal`, runs
:meth:`~services.security.runtime.RuntimeEnforcer.authorize`, and invokes the upstream
tool only on a permit.

The upstream checkout is never modified. It is imported by path and its pinned SHA-256
digests are verified at load time (see :mod:`demos.mediated_agent.upstream`), so a run
records whether the substrate was intact.

The comparison, stated honestly
-------------------------------
This is *not* "the same agent before and after" in the sense of a code-identical binary;
it is the same agent with one node replaced::

    raw upstream agent  vs  upstream agent behind a host-owned enforcement boundary

Everything else the benchmark controls — corpus, markers, classifier, report semantics,
model, sampling, case order — is held constant. The treatment is the boundary.

What is deliberately *not* enforced
-----------------------------------
Per-customer resource ownership and approval gating are **off**. The pinned target has no
authenticated principal and its order store has no owner column, so there is no grounded
source for "does this caller own this order?". Inventing one would either be derived from
the benchmark corpus (cheating) or arbitrary (a confound). Both are worse than the
limitation. See ``docs/phase4-runtime-enforcement-report.md`` for the pre-registration.
"""

from __future__ import annotations

MEDIATED_TARGET_VERSION = "mediated-support-agent-1"

__all__ = ["MEDIATED_TARGET_VERSION"]
