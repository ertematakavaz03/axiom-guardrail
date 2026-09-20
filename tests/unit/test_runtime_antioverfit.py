"""Mandatory anti-overfitting audit for the Phase 4 runtime enforcement layer.

A defence that passes a benchmark because it recognises the benchmark is not a defence.
These tests make that failure mode unrepresentable rather than merely discouraged: they
read the runtime modules' source and AST and assert that no benchmark identity can reach
a decision.

Four properties, each asserted structurally rather than by reading comments:

1. no benchmark scenario identifier (``rsa-``/``rsb-``) appears in runtime code;
2. no benchmark family name or gold/outcome label appears in runtime code;
3. the runtime modules import no benchmark corpus, evaluator, classifier or report;
4. the runtime modules import nothing from ``demos`` or ``benchmarks`` at all.

If a future change makes enforcement depend on the benchmark, one of these goes red.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import demos.mediated_agent.enforced_tools as mediated_node_module
import demos.mediated_agent.graph as mediated_graph_module
import demos.mediated_agent.policy as mediated_policy_module
import demos.mediated_agent.upstream as mediated_upstream_module
import services.security.egress as egress_module
import services.security.runtime as runtime_module

#: The production enforcement surface added in Phase 4. Anything listed here ships in the
#: product and must therefore know nothing about how the product is measured.
RUNTIME_MODULES = (runtime_module, egress_module)

#: Benchmark scenario identifiers, e.g. rsa-001 / rsb-025.
SCENARIO_ID = re.compile(r"\brs[ab]-\d{3}\b")

#: Attack and benign family names from the frozen corpus. Their presence in runtime code
#: would mean the enforcement layer branches on how the harness labels traffic.
BENCHMARK_FAMILIES = (
    "direct_injection",
    "role_impersonation",
    "policy_override",
    "unauthorized_action",
    "argument_manipulation",
    "indirect_injection",
    "system_prompt_extraction",
    "canary_exfiltration",
    "multi_turn_escalation",
    "foreign_identifier_access",
    "normal_read",
    "status_query",
    "scoped_access",
    "authorized_action",
    "normal_retrieval",
    "security_vocabulary",
    "hostile_tone",
    "long_instruction",
    "ambiguous_request",
    "normal_multi_turn",
)

#: Adjudicated outcome labels. These are the evaluator's answers; enforcement must decide
#: before, and independently of, any of them.
BENCHMARK_OUTCOMES = (
    "ATTACK_SUCCEEDED",
    "ATTACK_REJECTED_BY_AGENT",
    "ATTACK_INFLUENCED_NO_UNSAFE_ACTION",
    "UNSAFE_ACTION_ATTEMPTED",
    "MANUAL_REVIEW_REQUIRED",
    "SAFE_BEHAVIOR",
    "expected_detection_labels",
    "intended_violation",
    "gold",
)

#: Module prefixes that carry benchmark knowledge.
FORBIDDEN_IMPORT_PREFIXES = ("demos", "benchmarks")

#: Specific measurement modules, named so a failure message is obvious.
FORBIDDEN_IMPORT_MODULES = (
    "services.security.evaluator",
    "services.security.trace_detector",
    "services.security.runner",
)


def _source(module: object) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]


def _executable_source(module: object) -> str:
    """Source with docstrings and comments removed.

    Prose may legitimately *discuss* benchmark concepts — the module docstring explains
    that enforcement must not depend on them. What must be absent is a benchmark name the
    code can act on, so the audit scans what actually executes.
    """
    tree = ast.parse(_source(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)  # comments are not represented in the AST


def _imports(module: object) -> set[str]:
    tree = ast.parse(_source(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", RUNTIME_MODULES, ids=lambda m: m.__name__)
def test_no_benchmark_scenario_ids_in_runtime_code(module: object) -> None:
    found = SCENARIO_ID.findall(_executable_source(module))
    assert found == [], f"{module.__name__} references benchmark scenario ids: {found}"


@pytest.mark.parametrize("module", RUNTIME_MODULES, ids=lambda m: m.__name__)
def test_no_benchmark_family_names_in_runtime_code(module: object) -> None:
    source = _executable_source(module)
    found = [name for name in BENCHMARK_FAMILIES if name in source]
    assert found == [], f"{module.__name__} branches on benchmark family names: {found}"


@pytest.mark.parametrize("module", RUNTIME_MODULES, ids=lambda m: m.__name__)
def test_no_benchmark_outcome_labels_in_runtime_code(module: object) -> None:
    source = _executable_source(module)
    found = [label for label in BENCHMARK_OUTCOMES if label in source]
    assert found == [], f"{module.__name__} references evaluator outcomes/gold: {found}"


@pytest.mark.parametrize("module", RUNTIME_MODULES, ids=lambda m: m.__name__)
def test_runtime_code_imports_no_benchmark_package(module: object) -> None:
    offending = sorted(
        name for name in _imports(module) if name.split(".")[0] in FORBIDDEN_IMPORT_PREFIXES
    )
    assert offending == [], f"{module.__name__} imports benchmark code: {offending}"


@pytest.mark.parametrize("module", RUNTIME_MODULES, ids=lambda m: m.__name__)
def test_runtime_code_imports_no_evaluator_or_detector(module: object) -> None:
    offending = sorted(_imports(module) & set(FORBIDDEN_IMPORT_MODULES))
    assert offending == [], (
        f"{module.__name__} imports a measurement module: {offending}. Enforcement must "
        "decide before, and independently of, anything that scores the run."
    )


def test_the_audit_itself_would_catch_a_planted_violation(tmp_path: Path) -> None:
    """Guard against the audit silently passing because its patterns stopped matching."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "from demos.security_real_agent.corpus import corpus\n"
        "SPECIAL_CASE = 'rsa-001'\n"
        "FAMILY = 'direct_injection'\n"
        "OUTCOME = 'ATTACK_SUCCEEDED'\n",
        encoding="utf-8",
    )
    source = planted.read_text(encoding="utf-8")
    assert SCENARIO_ID.findall(source) == ["rsa-001"]
    assert any(name in source for name in BENCHMARK_FAMILIES)
    assert any(label in source for label in BENCHMARK_OUTCOMES)
    tree = ast.parse(source)
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert any((name or "").split(".")[0] in FORBIDDEN_IMPORT_PREFIXES for name in modules)


def test_enforcement_decides_from_trusted_state_and_nothing_else() -> None:
    """The decision surface takes a proposal and a context. There is no third input.

    This is the behavioural counterpart to the source-level checks above: even if a future
    author wanted to consult a label, ``authorize`` is not handed one.
    """
    import inspect

    signature = inspect.signature(runtime_module.RuntimeEnforcer.authorize)
    assert list(signature.parameters) == ["self", "proposal"]

    fields = set(runtime_module.ToolProposal.model_fields)
    assert fields == {"call_id", "tool", "arguments"}, (
        "A proposal carries only what a model can actually produce. Any extra field is a "
        f"channel for harness metadata: {fields}"
    )


# ======================================================================================
# The mediated target
#
# The enforcement layer above ships in the product. The mediated target is the thing the
# hardened benchmark points at, so it is subject to the same rule for the same reason: a
# target that recognises the benchmark would make the comparison meaningless even if the
# enforcement layer were spotless.
#
# Its import rule is necessarily different — the package imports itself — so the check is
# "no *other* demo package, and no benchmark package", rather than "nothing under demos".
# ======================================================================================

TARGET_MODULES = (
    mediated_policy_module,
    mediated_node_module,
    mediated_graph_module,
    mediated_upstream_module,
)

#: The mediated target's own package. Everything else under ``demos`` carries benchmark
#: knowledge and is forbidden.
SELF_PACKAGE = "demos.mediated_agent"


@pytest.mark.parametrize("module", TARGET_MODULES, ids=lambda m: m.__name__)
def test_no_benchmark_scenario_ids_in_the_mediated_target(module: object) -> None:
    found = SCENARIO_ID.findall(_executable_source(module))
    assert found == [], f"{module.__name__} references benchmark scenario ids: {found}"


@pytest.mark.parametrize("module", TARGET_MODULES, ids=lambda m: m.__name__)
def test_no_benchmark_family_or_outcome_names_in_the_mediated_target(module: object) -> None:
    source = _executable_source(module)
    found = [name for name in (*BENCHMARK_FAMILIES, *BENCHMARK_OUTCOMES) if name in source]
    assert found == [], f"{module.__name__} branches on benchmark labels: {found}"


@pytest.mark.parametrize("module", TARGET_MODULES, ids=lambda m: m.__name__)
def test_the_mediated_target_imports_no_benchmark_or_evaluator_code(module: object) -> None:
    offending = sorted(
        name
        for name in _imports(module)
        if (name.split(".")[0] in FORBIDDEN_IMPORT_PREFIXES and not name.startswith(SELF_PACKAGE))
        or name in FORBIDDEN_IMPORT_MODULES
    )
    assert offending == [], (
        f"{module.__name__} imports benchmark or measurement code: {offending}. The "
        "target under test must not be able to see how it is scored."
    )


def test_the_account_fixture_is_declared_data_not_a_lookup_into_the_corpus() -> None:
    """The one hand-supplied value in the mediated policy is a literal, and auditable.

    It is the account fixture, it is operator-supplied and pre-registered, and it is a
    plain tuple of identifiers. What this asserts is that it is not *computed* — there is
    no code path that could derive it from a corpus, a gold label or a prior run.
    """
    source = _executable_source(mediated_policy_module)
    tree = ast.parse(source)
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and any(
            getattr(target, "id", None) == "OWNED_ORDERS"
            for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        )
    )
    assert isinstance(assignment.value, ast.Tuple), "the fixture must be a literal"
    assert all(isinstance(item, ast.Constant) for item in assignment.value.elts)
    assert mediated_policy_module.OWNED_ORDERS == tuple(
        str(item.value) for item in assignment.value.elts
    )


def test_the_enforcement_node_decides_before_it_executes() -> None:
    """Structural: the permit check precedes every executor call in the node's source.

    A future edit that invokes a tool first and checks afterwards would satisfy every
    behavioural test that only inspects the returned message, because the refusal would
    still be returned — after the side effect had happened.
    """
    source = _executable_source(mediated_node_module)
    permit_at = source.index("permits_execution")
    invoke_at = source.index("tool.invoke")
    assert permit_at < invoke_at, "the executor is reached before the permit is checked"
