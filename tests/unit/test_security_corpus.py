from __future__ import annotations

from collections import Counter

import pytest

from demos.security_lab.corpus import corpus, demo_policy, mutate
from services.security.models import Category, Outcome, Principal
from services.security.runner import run_demo_case


def test_corpus_coverage_and_mutation_reproducibility() -> None:
    cases = corpus()
    assert len(cases) == len({item.id for item in cases}) == 78
    assert Counter(item.security_category for item in cases if item.is_attack) == dict.fromkeys(
        Category, 5
    )
    assert len({item.input for item in cases if item.is_attack}) == 65
    for kind in ["paraphrase", "role_play", "urgency", "sandwich", "nested_quote", "base64"]:
        assert mutate(cases[0], kind) == mutate(cases[0], kind)
        assert mutate(cases[0], kind).mutation_provenance["parent_id"] == cases[0].id


@pytest.mark.parametrize("case", corpus(), ids=lambda case: case.id)
async def test_demo_observational_and_preventive(case) -> None:
    principal = Principal(
        tenant_id="tenant-a",
        project_id="project-a",
        user_id="user-a",
        run_id=case.id,
        permissions={"refund"},
    )
    observed = await run_demo_case(case, demo_policy(), principal, "observational")
    prevented = await run_demo_case(case, demo_policy(), principal, "preventive")
    assert all(f.handling == "DETECTED_ONLY" for f in observed.evaluation.findings)
    for result in [observed, prevented]:
        event_ids = {event.id for event in result.events}
        assert all(set(f.event_ids) <= event_ids for f in result.evaluation.findings)
    if not case.is_attack:
        assert observed.evaluation.verdict == prevented.evaluation.verdict == "pass"
        assert observed.evaluation.outcome == prevented.evaluation.outcome == Outcome.NA
    else:
        assert observed.evaluation.findings
        assert prevented.evaluation.findings
    if case.setup.get("confirmation") == "replay":
        assert len(prevented.sandbox_effects) == 1
        decisions = [e.payload for e in prevented.events if e.kind == "policy_decision"]
        assert [d["decision"] for d in decisions] == ["ALLOW", "REQUIRE_CONFIRMATION"]
        assert decisions[1]["reasons"] == ["CONFIRMATION_BYPASS"]
