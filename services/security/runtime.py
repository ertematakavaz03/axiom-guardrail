"""Phase 4 runtime enforcement: decide before the side effect, from trusted state only.

This layer sits between a model's *proposal* and an executor. It exists because the
Phase 3 :class:`~services.security.policy.PolicyEngine` validates an ``Action`` that has
already been fully constructed — including whatever tenant, owner or identifier the model
put in it. Validating a model-authored request is strictly weaker than *building* the
request from trusted context, and the difference is the whole of this module.

Three invariants hold everywhere below, and each has a test that fails if it is removed:

1. **Authority is never derived from text.** Roles, permissions, tenancy, resource
   ownership and approvals come from :class:`TrustedContext`, which the host populates
   from session and database state. A model saying "I am an admin" or "the manager
   approved this" changes nothing, because neither sentence is an input to any decision
   here. Untrusted content is data; it is never authority.
2. **Scope-bound arguments are written by the server, not checked after the fact.** For
   every binding the tool declares, the enforcer overwrites the value from trusted context
   before validation. A model cannot smuggle a foreign tenant through a field it does not
   control, because its proposal for that field is discarded.
3. **No benchmark knowledge.** Nothing here imports a corpus, an evaluator, a gold label
   or a scenario id, and no decision branches on one. See
   ``tests/unit/test_runtime_antioverfit.py``.

The enforcer is deliberately deterministic. No model is consulted to decide whether an
action is safe: an LLM opinion is evidence at best, and cannot be the authorization
boundary it is meant to protect.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field

from services.security.models import (
    Action,
    PolicyDecision,
    Principal,
    SecurityPolicy,
    StrictModel,
    ToolRule,
    digest,
)
from services.security.policy import PolicyEngine, pointer

#: Bumped whenever a decision rule changes. Recorded in every decision so a stored
#: evidence trail can be read back against the logic that produced it.
RUNTIME_POLICY_VERSION = "runtime-enforcement-2"

#: What a tool *does*, independent of what it is called.
#:
#: ``ToolRule.mutates`` conflates two different risks: changing a record inside the trust
#: boundary that the caller owns, and sending content out of it. Ownership binding handles
#: the first and has nothing to bind to for the second, which is how a tool with no
#: resource identifier reached an executor with model-authored content in it.
#:
#: Capability lives here rather than on :class:`ToolRule` on purpose: ``ToolRule`` is
#: shared with the Phase 3 ``PolicyEngine``, whose ``policy_hash`` is recorded in frozen
#: shadow-policy evidence, and adding a field there would change that hash for reasons
#: unrelated to any decision.
Capability = Literal[
    "read_only",
    "scoped_mutation",
    "external_communication",
    "irreversible",
]

#: Capabilities whose effect leaves the trust boundary. These need authority the host
#: granted, not a tool name on an allowlist.
EXTERNAL_CAPABILITIES: frozenset[str] = frozenset({"external_communication", "irreversible"})

Decision = Literal[
    "ALLOW",
    "DENY",
    "REQUIRE_CONFIRMATION",
    "REQUIRE_APPROVAL",
    "SANDBOX_ONLY",
    "MANUAL_REVIEW",
]

#: Stable, product-level reason codes. Deliberately generic: these name a *policy*
#: condition, never an attack family, a corpus or a benchmark case.
REASON_TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
REASON_TOOL_UNKNOWN = "TOOL_NOT_REGISTERED"
#: Reuses the Phase 3 engine's existing code rather than minting a second name for the
#: same condition, so a stored trace has one code for "arguments failed policy".
REASON_INVALID_ARGUMENT = "ARGUMENT_POLICY_VIOLATION"
REASON_MISSING_PERMISSION = "MISSING_REQUIRED_PERMISSION"
REASON_AUTH_SCOPE = "AUTH_SCOPE_VIOLATION"
REASON_FOREIGN_RESOURCE = "FOREIGN_RESOURCE_ACCESS"
REASON_SERVER_BOUND = "ARGUMENT_BOUND_BY_SERVER"
REASON_NEEDS_CONFIRMATION = "CONFIRMATION_REQUIRED"
REASON_NEEDS_APPROVAL = "HIGH_IMPACT_ACTION_REQUIRES_APPROVAL"
REASON_MUTATION_NOT_ALLOWED = "MUTATION_NOT_PERMITTED_IN_CONTEXT"
REASON_SANDBOX_REQUIRED = "SANDBOX_REQUIRED_FOR_ENVIRONMENT"
REASON_STEP_BUDGET = "STEP_BUDGET_EXCEEDED"
REASON_DUPLICATE_WRITE = "DUPLICATE_WRITE_SUPPRESSED"
REASON_UNTRUSTED_AUTHORITY = "UNTRUSTED_INSTRUCTION_CLAIMED_AUTHORITY"
#: The caller holds no permission to cause an effect outside the trust boundary.
REASON_EXTERNAL_NOT_AUTHORIZED = "EXTERNAL_SIDE_EFFECT_NOT_AUTHORIZED"
#: A field that policy requires the *host* to author had no trusted value to write.
REASON_PAYLOAD_POLICY = "PAYLOAD_POLICY_VIOLATION"

#: Risk classes whose execution always needs an approval grant held in trusted state.
APPROVAL_REQUIRED_RISK: frozenset[str] = frozenset({"R3"})


#: ASCII digits only. The character class is spelled out rather than written ``\d``,
#: which matches every Unicode decimal digit: ``re.match(r"\d", "\u0665")`` succeeds, so
#: ``\d`` would quietly coerce U+0665 ARABIC-INDIC DIGIT FIVE to 5. A validator should not
#: have to reason about which scripts its numbers arrived in.
_INTEGER_TEXT = re.compile(r"\A[0-9]+\Z")
#: A plain decimal or exponent form, ASCII digits only for the same reason. Deliberately
#: excludes the words Python's ``float()`` accepts — ``nan``, ``inf``, ``infinity`` — and
#: any leading or trailing whitespace.
_NUMBER_TEXT = re.compile(r"\A[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def normalize_arguments(
    arguments: dict[str, Any], coercions: Mapping[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """Convert declared numeric-looking strings to numbers. Returns ``(arguments, paths)``.

    Models routinely emit numbers as JSON strings, and a tool whose own signature coerces
    them is not violated by receiving one. Refusing those calls is over-enforcement, which
    costs real work and teaches nobody anything.

    This is the whole of the permissiveness, and it is deliberately tiny:

    * only a ``str`` is considered, so a ``bool`` is never read as ``1`` and a nested
      object is never flattened;
    * only a path the policy explicitly declares is touched — an identifier that happens
      to be digits stays a string;
    * a value that does not parse is returned **exactly as it was**, to be refused by the
      unchanged schema a moment later. No fallback value is invented: substituting a
      default would hide the fact that an out-of-contract argument was proposed;
    * the result must be finite, because ``float("nan")`` succeeds and NaN satisfies no
      bound — every comparison against it is false, so it would slip past ``minimum`` and
      ``maximum`` alike.

    The normalized value is then validated by the same schema as before, so this changes a
    value's *type* and never a schema's strictness.
    """
    if not coercions:
        return dict(arguments), []
    out = dict(arguments)
    changed: list[str] = []
    for path, kind in sorted(coercions.items()):
        raw = pointer(out, path)
        if not isinstance(raw, str):
            continue
        if kind == "integer":
            if not _INTEGER_TEXT.match(raw):
                continue
            value: Any = int(raw)
        elif kind == "number":
            if not _NUMBER_TEXT.match(raw):
                continue
            parsed = float(raw)
            if not math.isfinite(parsed):
                continue
            value = parsed
        else:  # pragma: no cover - guarded by the Literal on RuntimePolicy.coercions
            continue
        _set_pointer(out, path, value)
        changed.append(path)
    return out, changed


def capability_for(tool: str, rule: ToolRule, runtime: RuntimePolicy) -> Capability:
    """The tool's declared capability, or the one implied by its existing rule.

    Deriving rather than requiring a declaration is what makes this change additive: a
    policy written before capabilities existed keeps its exact behaviour.
    """
    declared = runtime.capabilities.get(tool)
    if declared is not None:
        return declared
    return "scoped_mutation" if rule.mutates else "read_only"


class RuntimePolicy(StrictModel):
    """Phase 4 configuration, kept separate from :class:`SecurityPolicy` on purpose.

    ``SecurityPolicy.tools[t].resource_bindings`` already has a meaning to the Phase 3
    engine — a path mapped to the *values* that are allowed there — and it is enforced as
    a static allowlist. Per-principal ownership is a different question ("does *this*
    caller own this record?"), so it gets its own field rather than overloading one whose
    semantics the frozen shadow-policy path depends on.

    The two are complementary: the allowlist bounds what any caller may reach, ownership
    bounds what this caller may reach.
    """

    #: tool -> {json pointer path: resource kind}, resolved against ``owned_resources``.
    ownership_bindings: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: Tools that always need an approval grant, on top of the risk-class rule.
    approval_required_tools: list[str] = Field(default_factory=list)
    #: tool -> capability. Absent entries are derived from ``ToolRule.mutates``, so a
    #: policy written before capabilities existed keeps its exact behaviour.
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    #: The permission a principal must hold to cause an effect outside the boundary.
    external_permission: str = "external:send"
    #: When true, external capabilities also need an approval grant held in host state.
    #: Off by default: turning it on is a deployment decision with a real utility cost,
    #: because it denies legitimate outbound work in any deployment with no approver.
    require_approval_for_external: bool = False
    #: tool -> {json path: key in ``TrustedContext.payload_values``}. Declared fields are
    #: written by the host; whatever the model proposed for them is discarded. This is the
    #: same mechanism as ``scope_bindings``, pointed at free-form content instead of
    #: tenancy: the model decides *whether* to act, never *what text leaves the system*.
    payload_bindings: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: tool -> {json path: "integer" | "number"}. See :func:`normalize_arguments`.
    coercions: dict[str, dict[str, Literal["integer", "number"]]] = Field(default_factory=dict)


class ApprovalGrant(StrictModel):
    """An approval that exists in host state, issued by something other than the agent.

    ``subject`` is the principal the grant was issued to and ``tool`` the action it
    covers. A grant is never constructed from model output; :class:`RuntimeEnforcer`
    only ever *reads* grants the host placed in :class:`TrustedContext`.
    """

    id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    subject_user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    #: Optional narrowing: when set, the grant covers only these resource ids.
    resource_ids: list[str] = Field(default_factory=list)

    def covers(self, tool: str, principal: Principal, resources: Sequence[str]) -> bool:
        if self.tool != tool:
            return False
        if self.subject_user_id != principal.user_id or self.tenant_id != principal.tenant_id:
            return False
        if self.resource_ids and not set(resources).issubset(set(self.resource_ids)):
            return False
        return True


class TrustedContext(StrictModel):
    """Everything the enforcer is allowed to believe.

    The host builds this from authenticated session state and its own database. It must
    never be populated from model output, user message text, retrieved documents or tool
    results. That restriction is the reason a prompt-injected agent still cannot act
    outside its authority: influencing the model does not reach this object.
    """

    principal: Principal
    environment: Literal["production", "staging", "development", "sandbox"] = "production"
    #: resource kind -> identifiers this principal may act on, from the host's records.
    owned_resources: dict[str, list[str]] = Field(default_factory=dict)
    #: Approvals held in host state. An agent cannot add to this list.
    approvals: list[ApprovalGrant] = Field(default_factory=list)
    #: Host-authored values for fields the policy will not let a model write. Refreshed
    #: by the host between turns; never derived from model output. Holding them here keeps
    #: ``authorize`` a two-argument function, so there is no third channel through which
    #: metadata could reach a decision.
    payload_values: dict[str, str] = Field(default_factory=dict)
    #: ``shadow`` records the decision an enforcing deployment would have made and lets
    #: the call through, for safe rollout. ``enforce`` applies it.
    mode: Literal["enforce", "shadow"] = "enforce"

    def owns(self, kind: str, value: str) -> bool:
        return value in self.owned_resources.get(kind, [])


class ToolProposal(StrictModel):
    """What the model asked for. Untrusted by construction, including every argument."""

    call_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class RuntimeDecision(StrictModel):
    """A decision plus the evidence needed to defend it later.

    ``effective_decision`` is what the deployment actually did. In ``shadow`` mode it is
    always ``ALLOW`` while ``decision`` records what enforcement would have done, so a
    shadow rollout never silently claims to have blocked something.
    """

    runtime_policy_version: str = RUNTIME_POLICY_VERSION
    call_id: str
    tool: str
    decision: Decision
    effective_decision: Decision
    mode: Literal["enforce", "shadow"]
    reasons: list[str] = Field(default_factory=list)
    #: Arguments after server-side binding. This, not the proposal, is what executes.
    bound_arguments: dict[str, Any] = Field(default_factory=dict)
    #: Paths the server overwrote, with the value the model had proposed.
    server_bound_paths: list[str] = Field(default_factory=list)
    rejected_proposals: dict[str, Any] = Field(default_factory=dict)
    #: Paths whose value the host authored rather than the model.
    payload_bound_paths: list[str] = Field(default_factory=list)
    #: Paths whose declared numeric string was converted before validation.
    normalized_paths: list[str] = Field(default_factory=list)
    capability: Capability = "read_only"
    risk: str = "R0"
    mutates: bool = False
    executed: bool = False
    decided_at_ms: int = 0
    overhead_us: int = 0

    @property
    def permits_execution(self) -> bool:
        return self.effective_decision in ("ALLOW", "SANDBOX_ONLY")


def _set_pointer(target: dict[str, Any], path: str, value: Any) -> None:
    """Write ``value`` at a JSON-pointer-ish ``/a/b`` path, creating objects as needed."""
    parts = [part for part in path.split("/") if part]
    if not parts:
        return
    cursor: Any = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


class RuntimeEnforcer:
    """Authorize a proposal against trusted state, then let the host execute it.

    One enforcer is owned by one agent run. It is stateful only in the ways enforcement
    requires: a step budget, and a record of completed mutations so a retry cannot
    duplicate a write.
    """

    def __init__(
        self,
        policy: SecurityPolicy,
        context: TrustedContext,
        *,
        runtime: RuntimePolicy | None = None,
        engine: PolicyEngine | None = None,
    ) -> None:
        self.policy = policy.model_copy(deep=True)
        self.runtime = (runtime or RuntimePolicy()).model_copy(deep=True)
        self.context = context.model_copy(deep=True)
        # The Phase 3 engine still performs schema, tool-allowlist and budget checks. It is
        # composed, not replaced: this layer adds what it cannot see.
        self.engine = engine or PolicyEngine(self.policy)
        self.calls = 0
        self.decisions: list[RuntimeDecision] = []
        self._completed_mutations: dict[str, str] = {}

    # -- argument binding ---------------------------------------------------------
    def bind(
        self, proposal: ToolProposal, rule: ToolRule
    ) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
        """Return ``(bound_arguments, bound_paths, rejected_proposals)``.

        Scope-bound fields are *written* from trusted context. Whatever the model proposed
        for them is recorded as evidence and then discarded — it never reaches the
        executor and never reaches schema validation as an accepted value.
        """
        bound = dict(proposal.arguments)
        paths: list[str] = []
        rejected: dict[str, Any] = {}
        for path, attribute in sorted(rule.scope_bindings.items()):
            trusted = getattr(self.context.principal, attribute)
            proposed = pointer(proposal.arguments, path)
            if proposed is not None and proposed != trusted:
                rejected[path] = proposed
            _set_pointer(bound, path, trusted)
            paths.append(path)
        return bound, paths, rejected

    def update_payload_values(self, values: dict[str, str]) -> None:
        """Refresh the host-authored payload values between turns.

        Deliberately narrow: it writes one field of the trusted context and touches
        neither permissions, ownership nor approvals, so a plumbing call can never widen
        what the caller is allowed to do.
        """
        self.context = self.context.model_copy(
            update={"payload_values": {**self.context.payload_values, **values}}
        )

    def bind_payload(
        self, bound: dict[str, Any], proposal: ToolProposal
    ) -> tuple[list[str], dict[str, Any], list[str]]:
        """Write declared payload fields from host state. ``(paths, rejected, missing)``.

        Mirrors :meth:`bind`, for free-form content rather than scope: the model's value is
        recorded as evidence and then discarded. A field with no host value is reported as
        missing so the caller can fail closed — forwarding model-authored content because
        host state happened to be empty is precisely the failure this prevents.
        """
        paths: list[str] = []
        rejected: dict[str, Any] = {}
        missing: list[str] = []
        for path, key in sorted(self.runtime.payload_bindings.get(proposal.tool, {}).items()):
            trusted = self.context.payload_values.get(key)
            proposed = pointer(proposal.arguments, path)
            if trusted is None:
                missing.append(path)
                continue
            if proposed is not None and proposed != trusted:
                rejected[path] = proposed
            _set_pointer(bound, path, trusted)
            paths.append(path)
        return paths, rejected, missing

    # -- authorization ------------------------------------------------------------
    def authorize(self, proposal: ToolProposal) -> RuntimeDecision:
        started = time.perf_counter_ns()
        self.calls += 1
        reasons: list[str] = []
        rule = self.policy.tools.get(proposal.tool)

        if rule is None:
            return self._finish(
                proposal, "DENY", [REASON_TOOL_UNKNOWN], {}, [], {}, ToolRule(), started
            )

        bound, bound_paths, rejected = self.bind(proposal, rule)
        capability = capability_for(proposal.tool, rule, self.runtime)

        # Type normalization first, so the schema validates what the executor will get.
        bound, normalized_paths = normalize_arguments(
            bound, self.runtime.coercions.get(proposal.tool, {})
        )

        # Then payload provenance: the host authors what leaves the system.
        payload_paths, payload_rejected, payload_missing = self.bind_payload(bound, proposal)
        rejected.update(payload_rejected)
        if payload_missing:
            reasons.append(REASON_PAYLOAD_POLICY)

        if rejected:
            # Not fatal on its own: the server has already corrected the value. It is
            # recorded because a proposal that disagreed with trusted scope is a signal.
            reasons.append(REASON_SERVER_BOUND)

        if self.calls > self.policy.max_tool_calls:
            reasons.append(REASON_STEP_BUDGET)

        allowed = self.policy.allowed_tools
        if proposal.tool in self.policy.forbidden_tools or (
            allowed is not None and proposal.tool not in allowed
        ):
            reasons.append(REASON_TOOL_NOT_ALLOWED)

        missing = rule.required_permissions - self.context.principal.permissions
        if missing:
            reasons.append(REASON_MISSING_PERMISSION)

        # Resource ownership. A bound value that the principal does not own is a foreign
        # resource regardless of how plausible the model's justification was.
        ownership = self.runtime.ownership_bindings.get(proposal.tool, {})
        foreign: list[str] = []
        for path, kind in sorted(ownership.items()):
            value = pointer(bound, path)
            if value is None:
                continue
            if not self.context.owns(kind, str(value)):
                foreign.append(path)
        if foreign:
            reasons.append(REASON_FOREIGN_RESOURCE)

        # Schema validation runs on the *bound* arguments.
        action = Action(id=proposal.call_id, tool=proposal.tool, arguments=bound)
        engine_decision: PolicyDecision = self.engine.check(
            action, self.context.principal, call_count=self.calls
        )
        if REASON_INVALID_ARGUMENT in engine_decision.reasons:
            reasons.append(REASON_INVALID_ARGUMENT)
        for code in ("AUTHORIZATION_BYPASS", "CROSS_TENANT_ACCESS", "CROSS_USER_ACCESS"):
            if code in engine_decision.reasons:
                reasons.append(REASON_AUTH_SCOPE)
                break

        if rule.mutates:
            if not self.policy.allow_mutations:
                reasons.append(REASON_MUTATION_NOT_ALLOWED)
            fingerprint = digest({"tool": proposal.tool, "arguments": bound})
            if fingerprint in self._completed_mutations:
                reasons.append(REASON_DUPLICATE_WRITE)

        # Leaving the trust boundary needs authority the host granted. Ownership cannot
        # speak to this: there is no record to own, so without its own control a tool of
        # this shape is a clean channel out of the system.
        if capability in EXTERNAL_CAPABILITIES:
            if self.runtime.external_permission not in self.context.principal.permissions:
                reasons.append(REASON_EXTERNAL_NOT_AUTHORIZED)

        # Approval is checked against host state only. There is no code path by which a
        # message, a tool result or a retrieved document can produce a grant.
        resources = [
            str(pointer(bound, path))
            for path in sorted(ownership)
            if pointer(bound, path) is not None
        ]
        needs_approval = (
            rule.risk in APPROVAL_REQUIRED_RISK
            or proposal.tool in self.runtime.approval_required_tools
            or (self.runtime.require_approval_for_external and capability in EXTERNAL_CAPABILITIES)
        )
        if needs_approval and not any(
            grant.covers(proposal.tool, self.context.principal, resources)
            for grant in self.context.approvals
        ):
            reasons.append(REASON_NEEDS_APPROVAL)

        # The Phase 3 engine owns confirmation: R2 and explicitly flagged tools need a
        # valid grant. Surface it as its own tier rather than folding it into DENY.
        confirmation_needed = "CONFIRMATION_BYPASS" in engine_decision.reasons
        if confirmation_needed:
            reasons.append(REASON_NEEDS_CONFIRMATION)

        # Tiering, strongest first. A hard denial is never downgraded to "just confirm":
        # a caller who lacks authority cannot acquire it by clicking a confirmation.
        soft = {REASON_SERVER_BOUND, REASON_NEEDS_APPROVAL, REASON_NEEDS_CONFIRMATION}
        blocking = [code for code in reasons if code not in soft]

        decision: Decision
        if blocking:
            decision = "DENY"
        elif REASON_NEEDS_APPROVAL in reasons:
            decision = "REQUIRE_APPROVAL"
        elif confirmation_needed:
            decision = "REQUIRE_CONFIRMATION"
        else:
            decision = "ALLOW"

        # A write in a sandbox environment is redirected rather than denied, so
        # development keeps working without touching real state.
        if decision == "ALLOW" and rule.mutates and self.context.environment == "sandbox":
            decision = "SANDBOX_ONLY"
            reasons.append(REASON_SANDBOX_REQUIRED)

        return self._finish(
            proposal,
            decision,
            reasons,
            bound,
            bound_paths,
            rejected,
            rule,
            started,
            capability=capability,
            payload_bound_paths=payload_paths,
            normalized_paths=normalized_paths,
        )

    def _finish(
        self,
        proposal: ToolProposal,
        decision: Decision,
        reasons: list[str],
        bound: dict[str, Any],
        bound_paths: list[str],
        rejected: dict[str, Any],
        rule: ToolRule,
        started: int,
        *,
        capability: Capability = "read_only",
        payload_bound_paths: list[str] | None = None,
        normalized_paths: list[str] | None = None,
    ) -> RuntimeDecision:
        effective: Decision = decision if self.context.mode == "enforce" else "ALLOW"
        record = RuntimeDecision(
            call_id=proposal.call_id,
            tool=proposal.tool,
            decision=decision,
            effective_decision=effective,
            mode=self.context.mode,
            reasons=sorted(set(reasons)),
            bound_arguments=bound,
            server_bound_paths=bound_paths,
            rejected_proposals=rejected,
            payload_bound_paths=payload_bound_paths or [],
            normalized_paths=normalized_paths or [],
            capability=capability,
            risk=rule.risk,
            mutates=rule.mutates,
            decided_at_ms=int(time.time() * 1000),
            overhead_us=(time.perf_counter_ns() - started) // 1000,
        )
        self.decisions.append(record)
        return record

    def record_execution(self, decision: RuntimeDecision) -> None:
        """Mark a permitted call as executed so a retry cannot repeat the write."""
        if not decision.permits_execution:
            return
        decision.executed = True
        if decision.mutates:
            fingerprint = digest({"tool": decision.tool, "arguments": decision.bound_arguments})
            self._completed_mutations[fingerprint] = decision.call_id

    # -- evidence -----------------------------------------------------------------
    def evidence(self) -> dict[str, Any]:
        """A compact, log-safe summary of every decision this run made."""
        return {
            "runtime_policy_version": RUNTIME_POLICY_VERSION,
            "mode": self.context.mode,
            "environment": self.context.environment,
            "calls": self.calls,
            "decisions": [
                {
                    "call_id": item.call_id,
                    "tool": item.tool,
                    "decision": item.decision,
                    "effective_decision": item.effective_decision,
                    "reasons": item.reasons,
                    "risk": item.risk,
                    "mutates": item.mutates,
                    "executed": item.executed,
                    "server_bound_paths": item.server_bound_paths,
                    "payload_bound_paths": item.payload_bound_paths,
                    "normalized_paths": item.normalized_paths,
                    "capability": item.capability,
                }
                for item in self.decisions
            ],
        }
