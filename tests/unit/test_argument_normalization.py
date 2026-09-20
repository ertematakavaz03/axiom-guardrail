"""D-015 — conservative argument normalization, and the lines it must not cross.

The model emits numbers as JSON strings. The pinned tool accepts that; Axiom's schema did
not, and refused 21 benign calls in `20260920-phase4-hardened-1`. Normalization closes
that gap in one controlled step *before* schema validation.

The interesting tests here are the negative ones. Anything that makes a validator more
permissive is a security change, so most of this file is about what normalization refuses
to touch: unparseable strings, non-finite floats, booleans, identifiers, undeclared paths,
and anything that would let a value dodge a bound.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.security.models import Principal, SecurityPolicy, ToolRule
from services.security.runtime import (
    REASON_INVALID_ARGUMENT,
    RuntimeEnforcer,
    RuntimePolicy,
    ToolProposal,
    TrustedContext,
    normalize_arguments,
)

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        "min_similarity_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["query"],
    "additionalProperties": False,
}

#: Mirrors the upstream contract: these two paths coerce, nothing else does.
COERCIONS = {
    "search": {"/max_results": "integer", "/min_similarity_score": "number"},
}


def _policy() -> SecurityPolicy:
    return SecurityPolicy(
        version="normalization-test",
        tools={
            "search": ToolRule(risk="R0", input_schema=SEARCH_SCHEMA),
            "lookup": ToolRule(
                risk="R0",
                input_schema={
                    "type": "object",
                    "properties": {"order_id": {"type": "string", "pattern": r"^[0-9]{6}$"}},
                    "required": ["order_id"],
                    "additionalProperties": False,
                },
            ),
        },
        allowed_tools=["search", "lookup"],
        max_tool_calls=50,
    )


def _enforcer() -> RuntimeEnforcer:
    return RuntimeEnforcer(
        _policy(),
        TrustedContext(
            principal=Principal(tenant_id="t", project_id="p", user_id="u", run_id="r"),
            environment="production",
        ),
        runtime=RuntimePolicy(coercions=COERCIONS),
    )


def _decide(arguments: dict[str, Any], tool: str = "search") -> Any:
    return _enforcer().authorize(ToolProposal(call_id="c1", tool=tool, arguments=arguments))


# -- the accepted forms, which are exactly the ones the pinned tool accepts -------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", 5), ("1", 1), ("10", 10), ("0", 0), ("007", 7)],
)
def test_a_digit_string_normalizes_to_an_integer(raw: str, expected: int) -> None:
    normalized, paths = normalize_arguments({"max_results": raw}, {"/max_results": "integer"})
    assert normalized["max_results"] == expected
    assert isinstance(normalized["max_results"], int)
    assert paths == ["/max_results"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.5", 0.5), ("0", 0.0), ("1", 1.0), (".5", 0.5), ("1e-2", 0.01), ("+0.25", 0.25)],
)
def test_a_decimal_string_normalizes_to_a_number(raw: str, expected: float) -> None:
    normalized, paths = normalize_arguments(
        {"min_similarity_score": raw}, {"/min_similarity_score": "number"}
    )
    assert normalized["min_similarity_score"] == pytest.approx(expected)
    assert paths == ["/min_similarity_score"]


def test_the_benign_call_that_hardened_1_refused_now_succeeds() -> None:
    """The exact argument shape behind 21 false blocks, minus anything case-specific."""
    decision = _decide(
        {"query": "return policy", "max_results": "5", "min_similarity_score": "0.5"}
    )
    assert decision.decision == "ALLOW"
    assert decision.bound_arguments["max_results"] == 5
    assert decision.bound_arguments["min_similarity_score"] == 0.5
    assert decision.normalized_paths == ["/max_results", "/min_similarity_score"]


def test_the_executor_receives_the_normalized_value_not_the_string() -> None:
    decision = _decide({"query": "x", "max_results": "3"})
    assert decision.bound_arguments["max_results"] == 3
    assert not isinstance(decision.bound_arguments["max_results"], str)


# -- and everything normalization must refuse to touch ---------------------------------
@pytest.mark.parametrize(
    "raw",
    ["abc", "5 items", "5.0.1", "0x10", " 5 ", "", "five", "5,0", "1_000", "٥", "-3"],
)
def test_an_unparseable_integer_string_is_left_alone_and_still_denied(raw: str) -> None:
    """Upstream silently substitutes 5 here. Axiom refuses, because a fallback hides
    the fact that an out-of-contract argument was proposed."""
    normalized, paths = normalize_arguments({"max_results": raw}, {"/max_results": "integer"})
    assert normalized["max_results"] == raw
    assert paths == []
    decision = _decide({"query": "x", "max_results": raw})
    assert decision.decision == "DENY"
    assert REASON_INVALID_ARGUMENT in decision.reasons


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity", "1e999"])
def test_a_non_finite_string_is_never_coerced(raw: str) -> None:
    """``float("nan")`` succeeds in Python. A validator that accepted it would let a
    value through that satisfies no bound, because every comparison against NaN is false."""
    normalized, paths = normalize_arguments(
        {"min_similarity_score": raw}, {"/min_similarity_score": "number"}
    )
    assert normalized["min_similarity_score"] == raw
    assert paths == []
    assert _decide({"query": "x", "min_similarity_score": raw}).decision == "DENY"


def test_a_boolean_is_not_read_as_a_number() -> None:
    """``True`` is an ``int`` in Python; treating it as 1 would be type confusion."""
    normalized, paths = normalize_arguments({"max_results": True}, {"/max_results": "integer"})
    assert normalized["max_results"] is True
    assert paths == []


@pytest.mark.parametrize("raw", [{"value": "5"}, ["5"], None])
def test_nested_and_null_values_are_untouched(raw: Any) -> None:
    normalized, paths = normalize_arguments({"max_results": raw}, {"/max_results": "integer"})
    assert normalized["max_results"] == raw
    assert paths == []


def test_a_path_with_no_declared_coercion_is_never_coerced() -> None:
    """An identifier that happens to be digits is not a number."""
    normalized, paths = normalize_arguments({"order_id": "345678"}, {})
    assert normalized["order_id"] == "345678"
    assert isinstance(normalized["order_id"], str)
    assert paths == []
    decision = _decide({"order_id": "345678"}, tool="lookup")
    assert decision.decision == "ALLOW"
    assert decision.bound_arguments["order_id"] == "345678"


def test_a_missing_path_does_not_create_the_key() -> None:
    normalized, paths = normalize_arguments({"query": "x"}, {"/max_results": "integer"})
    assert "max_results" not in normalized
    assert paths == []


# -- the security property: normalization may not widen a schema -----------------------
@pytest.mark.parametrize("raw", ["999", "0", "11", "-1"])
def test_a_normalized_value_still_faces_the_unchanged_bound(raw: str) -> None:
    """`"999"` becomes the integer 999 and is then refused by `maximum: 10`.

    This is the whole safety argument for normalization: it changes a value's *type*, and
    then hands it to exactly the same validator as before.
    """
    decision = _decide({"query": "x", "max_results": raw})
    assert decision.decision == "DENY"
    assert REASON_INVALID_ARGUMENT in decision.reasons


def test_a_normalized_number_still_faces_its_range() -> None:
    assert _decide({"query": "x", "min_similarity_score": "1.5"}).decision == "DENY"
    assert _decide({"query": "x", "min_similarity_score": "-0.5"}).decision == "DENY"
    assert _decide({"query": "x", "min_similarity_score": "1.0"}).decision == "ALLOW"


def test_normalization_does_not_defeat_additional_properties() -> None:
    decision = _decide({"query": "x", "max_results": "5", "unexpected": "1"})
    assert decision.decision == "DENY"
    assert REASON_INVALID_ARGUMENT in decision.reasons


def test_normalization_is_recorded_in_the_run_evidence() -> None:
    enforcer = _enforcer()
    enforcer.authorize(
        ToolProposal(call_id="c1", tool="search", arguments={"query": "x", "max_results": "5"})
    )
    [record] = enforcer.evidence()["decisions"]
    assert record["normalized_paths"] == ["/max_results"]


def test_a_run_with_no_coercions_configured_behaves_exactly_as_before() -> None:
    """Additive by construction: an unconfigured deployment is unchanged."""
    enforcer = RuntimeEnforcer(
        _policy(),
        TrustedContext(
            principal=Principal(tenant_id="t", project_id="p", user_id="u", run_id="r"),
            environment="production",
        ),
        runtime=RuntimePolicy(),
    )
    decision = enforcer.authorize(
        ToolProposal(call_id="c1", tool="search", arguments={"query": "x", "max_results": "5"})
    )
    assert decision.decision == "DENY"
    assert decision.normalized_paths == []
