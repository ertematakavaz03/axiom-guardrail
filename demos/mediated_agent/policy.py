"""The policy the mediated deployment enforces, and where every clause comes from.

Provenance matters more than content here. Each schema bound below is traceable to the
pinned upstream tool's *own* documented contract — its signature and its docstring — not
to anything in the benchmark corpus. Nothing in this module reads a scenario, a family,
a gold label or an evaluator outcome.

The one clause that cannot be derived from upstream source is the account fixture: the
pinned order store has no owner column and the target has no authenticated principal, so
"which orders belong to this customer?" has no upstream answer. That value was supplied
by the operator and pre-registered verbatim before the hardened run (see
``docs/phase4-runtime-enforcement-report.md``). It is recorded here as data, with its
provenance stated, precisely so a reader can audit it rather than take it on trust.
"""

from __future__ import annotations

from typing import Any

from services.security.models import SecurityPolicy, ToolRule
from services.security.runtime import RuntimePolicy

#: Policy version recorded in every decision. Bumped when a clause below changes.
MEDIATED_POLICY_VERSION = "mediated-support-1"

#: The authenticated customer this deployment serves.
ACCOUNT_USER_ID = "customer-1"
ACCOUNT_TENANT_ID = "tenant-demo"

#: ---------------------------------------------------------------------------------
#: PRE-REGISTERED ACCOUNT FIXTURE
#:
#: Operator-supplied, fixed before the hardened run and never revised after seeing a
#: result. The upstream order store (``MOCK_ORDERS``) declares 21 orders and no owner
#: column; these seven are the ones the deployment's authenticated customer owns. Every
#: other identifier in the store is a foreign resource, and the enforcer denies access to
#: it no matter how the request is phrased.
#:
#: This list was NOT derived from the benchmark corpus. Deriving it from the corpus would
#: be fitting the defence to the test; deriving it from a rule of Claude's own choosing
#: would leave a free parameter that could have been tuned. The overlap between this
#: fixture and the identifiers the corpus happens to use is reported after the run.
#: ---------------------------------------------------------------------------------
OWNED_ORDERS: tuple[str, ...] = (
    "345678",
    "234567",
    "123456",
    "678901",
    "789012",
    "223344",
    "334455",
)

#: The eight tools the pinned target exposes, in the upstream's own declaration order.
#: Sourced from ``support_agent.tools.tools``; asserted against it at load time so a
#: drift in the upstream surface fails loudly instead of silently under-enforcing.
TOOL_NAMES: tuple[str, ...] = (
    "list_available_functions",
    "send_greeting",
    "search_vector_knowledge_base",
    "get_order_status",
    "list_orders",
    "initiate_return",
    "check_product_availability",
    "escalate_to_human",
)

#: Step budget for one case. A bound on runaway loops, set generously: the longest
#: authored conversation is a handful of turns and the upstream agent issues a small
#: number of calls per turn. It is a product control against unbounded tool use, not a
#: threshold fitted to any observed run.
MAX_TOOL_CALLS = 16

#: Six-digit order identifier, the only shape the upstream store contains.
_ORDER_ID = {"type": "string", "pattern": r"^[0-9]{6}$"}
_STRING = {"type": "string"}


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        # Closed by default: an argument the tool does not declare cannot be smuggled in.
        "additionalProperties": False,
    }


def security_policy() -> SecurityPolicy:
    """Tool contracts for the mediated target.

    Every bound is quoted from the pinned upstream source:

    * ``search_vector_knowledge_base`` — its docstring states *"max_results: Maximum
      number of results to return (1-10, default: 5)"* and *"min_similarity_score:
      Minimum similarity score threshold 0.0-1.0"*. The upstream implementation clamps
      both silently; enforcing them means an out-of-contract argument is refused and
      recorded rather than quietly corrected.
    * ``initiate_return`` — the upstream system prompt states *"When calling
      initiate_return, you MUST extract and provide both order_id AND reason"*, so both
      are required.
    * ``escalate_to_human(reason, customer_message)`` and
      ``check_product_availability(product_name)`` — required by signature.
    * order identifiers are six digits throughout ``MOCK_ORDERS``.

    Risk classes follow the pinned ``tool-map.json``: the two tools it marks with a side
    effect are the two mutating tools here.
    """
    return SecurityPolicy(
        version=MEDIATED_POLICY_VERSION,
        tools={
            "list_available_functions": ToolRule(risk="R0", input_schema=_object({})),
            "send_greeting": ToolRule(risk="R0", input_schema=_object({})),
            "search_vector_knowledge_base": ToolRule(
                risk="R0",
                input_schema=_object(
                    {
                        "query": _STRING,
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                        "min_similarity_score": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "categories": _STRING,
                    },
                    ["query"],
                ),
            ),
            "get_order_status": ToolRule(
                risk="R0", input_schema=_object({"order_id": _ORDER_ID}, ["order_id"])
            ),
            "list_orders": ToolRule(risk="R0", input_schema=_object({"status_filter": _STRING})),
            "initiate_return": ToolRule(
                risk="R1",
                input_schema=_object(
                    {"order_id": _ORDER_ID, "reason": {"type": "string", "minLength": 1}},
                    ["order_id", "reason"],
                ),
                mutates=True,
            ),
            "check_product_availability": ToolRule(
                risk="R0", input_schema=_object({"product_name": _STRING}, ["product_name"])
            ),
            "escalate_to_human": ToolRule(
                risk="R1",
                input_schema=_object(
                    {"reason": _STRING, "customer_message": _STRING},
                    ["reason", "customer_message"],
                ),
                mutates=True,
            ),
        },
        allowed_tools=list(TOOL_NAMES),
        # Mutations are permitted by policy and then constrained: ownership decides which
        # resource may be written, and the sandbox environment keeps the write simulated,
        # matching the pinned tool-map's ``simulated_non_persistent`` semantics.
        allow_mutations=True,
        max_tool_calls=MAX_TOOL_CALLS,
        max_retries=1,
        sensitive_data=[],
    )


def runtime_policy() -> RuntimePolicy:
    """Per-principal resource ownership.

    Both tools that take an order identifier resolve it against the account fixture. A
    read of someone else's order is as much a foreign-resource access as a write to it,
    so ``get_order_status`` is bound the same way ``initiate_return`` is.

    ``approval_required_tools`` is deliberately empty. An approval gate is only
    meaningful when the host has a real source of approvals; inventing one for a target
    with no authenticated approver would be theatre, and gating every write behind an
    approval that can never arrive would refuse legitimate work.
    """
    return RuntimePolicy(
        ownership_bindings={
            "get_order_status": {"/order_id": "order"},
            "initiate_return": {"/order_id": "order"},
        },
        approval_required_tools=[],
    )


def owned_resources() -> dict[str, list[str]]:
    """The trusted context's resource table, built from the pre-registered fixture."""
    return {"order": list(OWNED_ORDERS)}


def assert_tool_surface(upstream_tool_names: list[str]) -> None:
    """Fail loudly if the upstream surface is not the one this policy was written for.

    Under-enforcement by silent drift is the failure mode worth crashing over: a tool
    that appeared upstream after this policy was authored would otherwise be refused as
    unknown (safe) or, worse, a renamed tool would lose its ownership binding.
    """
    expected = set(TOOL_NAMES)
    actual = set(upstream_tool_names)
    if expected != actual:
        raise RuntimeError(
            "upstream tool surface does not match the mediated policy; "
            f"missing={sorted(expected - actual)} unexpected={sorted(actual - expected)}"
        )
