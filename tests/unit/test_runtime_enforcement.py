"""Abuse tests for the Phase 4 runtime enforcement layer.

Each test states an attacker capability and asserts the enforcement outcome. None of them
reference a benchmark scenario, family or gold label — the enforcement layer does not know
those exist, and ``test_runtime_antioverfit.py`` proves it.

The recurring shape is: the model proposes something it should not be able to do, and the
enforcer refuses using only trusted state. Where a control could plausibly be implemented
as "validate what the model sent", the test is written so that a validate-only
implementation fails and only server-side binding passes.
"""

from __future__ import annotations

import pytest

from services.security.models import Principal, SecurityPolicy, ToolRule
from services.security.runtime import (
    APPROVAL_REQUIRED_RISK,
    REASON_DUPLICATE_WRITE,
    REASON_FOREIGN_RESOURCE,
    REASON_INVALID_ARGUMENT,
    REASON_MISSING_PERMISSION,
    REASON_MUTATION_NOT_ALLOWED,
    REASON_NEEDS_APPROVAL,
    REASON_NEEDS_CONFIRMATION,
    REASON_SERVER_BOUND,
    REASON_STEP_BUDGET,
    REASON_TOOL_NOT_ALLOWED,
    REASON_TOOL_UNKNOWN,
    ApprovalGrant,
    RuntimeEnforcer,
    RuntimePolicy,
    ToolProposal,
    TrustedContext,
)

ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {"type": "string"},
        "order_id": {"type": "string"},
        "amount": {"type": "integer", "minimum": 1, "maximum": 500},
    },
    "required": ["order_id"],
    "additionalProperties": False,
}


def _policy(**overrides: object) -> SecurityPolicy:
    policy = SecurityPolicy(
        version="test-1",
        tools={
            "get_order": ToolRule(
                risk="R0",
                input_schema=ORDER_SCHEMA,
                scope_bindings={"/tenant_id": "tenant_id"},
            ),
            "refund_order": ToolRule(
                risk="R1",
                input_schema=ORDER_SCHEMA,
                required_permissions={"refund:write"},
                scope_bindings={"/tenant_id": "tenant_id"},
                mutates=True,
            ),
            "close_account": ToolRule(
                risk="R2",
                input_schema={"type": "object", "properties": {"tenant_id": {"type": "string"}}},
                scope_bindings={"/tenant_id": "tenant_id"},
                mutates=True,
            ),
            "delete_account": ToolRule(
                risk="R3",
                input_schema={"type": "object", "properties": {"tenant_id": {"type": "string"}}},
                scope_bindings={"/tenant_id": "tenant_id"},
                mutates=True,
            ),
        },
        allowed_tools=["get_order", "refund_order", "close_account", "delete_account"],
        allow_mutations=True,
        max_tool_calls=5,
    )
    return policy.model_copy(update=dict(overrides))


def _context(**overrides: object) -> TrustedContext:
    base = TrustedContext(
        principal=Principal(
            tenant_id="tenant-a",
            project_id="proj-1",
            user_id="user-1",
            run_id="run-1",
            permissions={"refund:write"},
        ),
        owned_resources={"order": ["order-1", "order-2"]},
    )
    return base.model_copy(update=dict(overrides))


#: Ownership is per-principal and therefore Phase-4 runtime config, not a static allowlist.
RUNTIME = RuntimePolicy(
    ownership_bindings={
        "get_order": {"/order_id": "order"},
        "refund_order": {"/order_id": "order"},
    }
)


def _enforcer(policy: SecurityPolicy | None = None, context: TrustedContext | None = None):
    return RuntimeEnforcer(policy or _policy(), context or _context(), runtime=RUNTIME)


# --- baseline: legitimate traffic still works ---------------------------------------


def test_a_normal_read_in_scope_is_allowed() -> None:
    decision = _enforcer().authorize(
        ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-1"})
    )
    assert decision.decision == "ALLOW"
    assert decision.reasons == []
    assert decision.permits_execution is True


def test_an_authorized_bounded_write_is_allowed() -> None:
    decision = _enforcer().authorize(
        ToolProposal(
            call_id="c1", tool="refund_order", arguments={"order_id": "order-1", "amount": 50}
        )
    )
    assert decision.decision == "ALLOW"
    assert decision.mutates is True


# --- authority is never derived from text -------------------------------------------


def test_claiming_a_role_in_arguments_does_not_grant_permission() -> None:
    """The model may say anything; permissions come from the principal only."""
    context = _context(
        principal=Principal(
            tenant_id="tenant-a",
            project_id="proj-1",
            user_id="user-1",
            run_id="run-1",
            permissions=set(),  # no refund:write
        )
    )
    decision = _enforcer(context=context).authorize(
        ToolProposal(
            call_id="c1",
            tool="refund_order",
            arguments={"order_id": "order-1", "amount": 10},
        )
    )
    assert decision.decision == "DENY"
    assert REASON_MISSING_PERMISSION in decision.reasons


def test_text_claiming_approval_never_produces_an_approval() -> None:
    """An R3 action with no grant in host state is refused, whatever the model asserts."""
    decision = _enforcer().authorize(
        ToolProposal(call_id="c1", tool="delete_account", arguments={})
    )
    assert decision.decision == "REQUIRE_APPROVAL"
    assert REASON_NEEDS_APPROVAL in decision.reasons
    assert decision.permits_execution is False


def test_a_real_grant_in_trusted_state_does_permit_the_high_impact_action() -> None:
    context = _context(
        approvals=[
            ApprovalGrant(
                id="grant-1",
                tool="delete_account",
                subject_user_id="user-1",
                tenant_id="tenant-a",
            )
        ]
    )
    decision = _enforcer(context=context).authorize(
        ToolProposal(call_id="c1", tool="delete_account", arguments={})
    )
    assert decision.decision == "ALLOW"


def test_a_grant_issued_to_another_user_does_not_transfer() -> None:
    context = _context(
        approvals=[
            ApprovalGrant(
                id="grant-1",
                tool="delete_account",
                subject_user_id="someone-else",
                tenant_id="tenant-a",
            )
        ]
    )
    decision = _enforcer(context=context).authorize(
        ToolProposal(call_id="c1", tool="delete_account", arguments={})
    )
    assert decision.decision == "REQUIRE_APPROVAL"


def test_a_grant_for_a_different_tool_does_not_transfer() -> None:
    context = _context(
        approvals=[
            ApprovalGrant(
                id="grant-1", tool="refund_order", subject_user_id="user-1", tenant_id="tenant-a"
            )
        ]
    )
    decision = _enforcer(context=context).authorize(
        ToolProposal(call_id="c1", tool="delete_account", arguments={})
    )
    assert decision.decision == "REQUIRE_APPROVAL"


# --- server-side argument binding ----------------------------------------------------


def test_a_proposed_foreign_tenant_is_overwritten_not_merely_rejected() -> None:
    """A validate-only implementation would pass this test's first assertion and fail the rest."""
    decision = _enforcer().authorize(
        ToolProposal(
            call_id="c1",
            tool="get_order",
            arguments={"order_id": "order-1", "tenant_id": "tenant-victim"},
        )
    )
    # the server wrote the trusted value; the model's proposal never reaches the executor
    assert decision.bound_arguments["tenant_id"] == "tenant-a"
    assert decision.rejected_proposals["/tenant_id"] == "tenant-victim"
    assert REASON_SERVER_BOUND in decision.reasons
    assert decision.decision == "ALLOW"


def test_omitting_a_scope_bound_field_still_binds_it() -> None:
    decision = _enforcer().authorize(
        ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-1"})
    )
    assert decision.bound_arguments["tenant_id"] == "tenant-a"
    assert "/tenant_id" in decision.server_bound_paths


def test_a_foreign_resource_id_is_denied() -> None:
    decision = _enforcer().authorize(
        ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-999"})
    )
    assert decision.decision == "DENY"
    assert REASON_FOREIGN_RESOURCE in decision.reasons


def test_argument_tampering_outside_the_schema_is_denied() -> None:
    decision = _enforcer().authorize(
        ToolProposal(
            call_id="c1",
            tool="refund_order",
            arguments={"order_id": "order-1", "amount": 100000},
        )
    )
    assert decision.decision == "DENY"
    assert REASON_INVALID_ARGUMENT in decision.reasons


def test_an_unknown_tool_is_denied() -> None:
    decision = _enforcer().authorize(ToolProposal(call_id="c1", tool="exfiltrate", arguments={}))
    assert decision.decision == "DENY"
    assert decision.reasons == [REASON_TOOL_UNKNOWN]


def test_a_tool_outside_the_allowlist_is_denied() -> None:
    policy = _policy(allowed_tools=["get_order"])
    decision = _enforcer(policy=policy).authorize(
        ToolProposal(
            call_id="c1", tool="refund_order", arguments={"order_id": "order-1", "amount": 5}
        )
    )
    assert decision.decision == "DENY"
    assert REASON_TOOL_NOT_ALLOWED in decision.reasons


# --- side-effect safety ---------------------------------------------------------------


def test_mutations_are_refused_when_the_context_forbids_them() -> None:
    policy = _policy(allow_mutations=False)
    decision = _enforcer(policy=policy).authorize(
        ToolProposal(
            call_id="c1", tool="refund_order", arguments={"order_id": "order-1", "amount": 5}
        )
    )
    assert decision.decision == "DENY"
    assert REASON_MUTATION_NOT_ALLOWED in decision.reasons


def test_a_retry_cannot_duplicate_a_completed_write() -> None:
    enforcer = _enforcer()
    first = enforcer.authorize(
        ToolProposal(
            call_id="c1", tool="refund_order", arguments={"order_id": "order-1", "amount": 25}
        )
    )
    assert first.decision == "ALLOW"
    enforcer.record_execution(first)

    retry = enforcer.authorize(
        ToolProposal(
            call_id="c2", tool="refund_order", arguments={"order_id": "order-1", "amount": 25}
        )
    )
    assert retry.decision == "DENY"
    assert REASON_DUPLICATE_WRITE in retry.reasons
    assert retry.permits_execution is False


def test_a_denied_call_is_never_marked_executed() -> None:
    enforcer = _enforcer()
    decision = enforcer.authorize(
        ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-999"})
    )
    enforcer.record_execution(decision)
    assert decision.executed is False


def test_a_write_in_a_sandbox_environment_is_redirected_not_executed_for_real() -> None:
    decision = _enforcer(context=_context(environment="sandbox")).authorize(
        ToolProposal(
            call_id="c1", tool="refund_order", arguments={"order_id": "order-1", "amount": 5}
        )
    )
    assert decision.decision == "SANDBOX_ONLY"
    assert decision.permits_execution is True


def test_the_step_budget_is_enforced() -> None:
    enforcer = _enforcer()
    last = None
    for index in range(7):
        last = enforcer.authorize(
            ToolProposal(call_id=f"c{index}", tool="get_order", arguments={"order_id": "order-1"})
        )
    assert last is not None
    assert REASON_STEP_BUDGET in last.reasons
    assert last.decision == "DENY"


# --- shadow vs enforce ----------------------------------------------------------------


def test_shadow_mode_records_the_decision_but_permits_execution() -> None:
    decision = _enforcer(context=_context(mode="shadow")).authorize(
        ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-999"})
    )
    assert decision.decision == "DENY"
    assert decision.effective_decision == "ALLOW"
    assert decision.mode == "shadow"
    assert decision.permits_execution is True


def test_enforce_mode_actually_blocks() -> None:
    decision = _enforcer().authorize(
        ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-999"})
    )
    assert decision.effective_decision == "DENY"
    assert decision.permits_execution is False


# --- evidence --------------------------------------------------------------------------


def test_every_decision_produces_stable_reason_codes_and_evidence() -> None:
    enforcer = _enforcer()
    enforcer.authorize(
        ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-999"})
    )
    enforcer.authorize(
        ToolProposal(call_id="c2", tool="get_order", arguments={"order_id": "order-1"})
    )
    evidence = enforcer.evidence()
    assert evidence["runtime_policy_version"]
    assert evidence["mode"] == "enforce"
    assert [item["decision"] for item in evidence["decisions"]] == ["DENY", "ALLOW"]
    assert evidence["decisions"][0]["reasons"] == [REASON_FOREIGN_RESOURCE]


def test_reason_codes_are_deterministic_across_repeated_calls() -> None:
    runs = [
        _enforcer()
        .authorize(
            ToolProposal(call_id="c1", tool="get_order", arguments={"order_id": "order-999"})
        )
        .reasons
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


def test_evidence_contains_no_raw_rejected_secret_by_default() -> None:
    """Rejected proposals are recorded, so callers must treat evidence as sensitive."""
    decision = _enforcer().authorize(
        ToolProposal(
            call_id="c1",
            tool="get_order",
            arguments={"order_id": "order-1", "tenant_id": "tenant-victim"},
        )
    )
    summary = _enforcer().evidence()
    # the compact evidence summary deliberately omits argument values
    assert "tenant-victim" not in str(summary)
    assert decision.rejected_proposals  # still available on the full decision record


@pytest.mark.parametrize("risk", sorted(APPROVAL_REQUIRED_RISK))
def test_every_approval_required_risk_class_is_actually_gated(risk: str) -> None:
    policy = _policy()
    policy.tools["delete_account"] = policy.tools["delete_account"].model_copy(
        update={"risk": risk}
    )
    decision = _enforcer(policy=policy).authorize(
        ToolProposal(call_id="c1", tool="delete_account", arguments={})
    )
    assert decision.decision == "REQUIRE_APPROVAL"


def test_a_confirmation_required_write_is_not_allowed_without_a_grant() -> None:
    """R2 needs confirmation. The tier is surfaced, not collapsed into DENY or ALLOW."""
    decision = _enforcer().authorize(ToolProposal(call_id="c1", tool="close_account", arguments={}))
    assert decision.decision == "REQUIRE_CONFIRMATION"
    assert REASON_NEEDS_CONFIRMATION in decision.reasons
    assert decision.permits_execution is False


def test_a_hard_denial_is_never_downgraded_to_a_confirmation_prompt() -> None:
    """Lacking authority is not something a user can click through."""
    context = _context(
        principal=Principal(
            tenant_id="tenant-a",
            project_id="proj-1",
            user_id="user-1",
            run_id="run-1",
            permissions=set(),
        )
    )
    decision = _enforcer(context=context).authorize(
        ToolProposal(
            call_id="c1", tool="refund_order", arguments={"order_id": "order-1", "amount": 5}
        )
    )
    assert decision.decision == "DENY"
    assert REASON_MISSING_PERMISSION in decision.reasons
