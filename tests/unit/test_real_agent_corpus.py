from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from demos.security_lab.corpus import corpus as conformance_corpus
from demos.security_real_agent.corpus import attacks, controls, corpus
from demos.security_real_agent.models import (
    FORBIDDEN_CONTROL_CHANNELS,
    AttackFamily,
    BenignFamily,
    GoldLabel,
    RealAgentScenario,
)
from demos.security_real_agent.runner import corpus_statistics
from services.security.reasons import REASONS


def test_no_scenario_prescribes_the_unsafe_action() -> None:
    """Acceptance criterion D: attack text must reach the model as plain language."""
    for scenario in corpus():
        visible = scenario.model_visible_input
        for marker in FORBIDDEN_CONTROL_CHANNELS:
            assert marker not in visible, scenario.id


def test_conformance_suite_still_uses_the_prescribed_channel() -> None:
    """Guards the distinction itself: the deterministic suite is unchanged and is not
    a natural-language benchmark."""
    prescribed = [case for case in conformance_corpus() if "AXIOM_COMMANDS=" in case.input]
    assert len(prescribed) == 71


def test_every_prompt_is_unique() -> None:
    scenarios = corpus()
    prompts = [scenario.model_visible_input for scenario in scenarios]
    duplicates = [prompt for prompt, count in Counter(prompts).items() if count > 1]
    assert duplicates == []
    assert len({scenario.id for scenario in scenarios}) == len(scenarios)


def test_benign_controls_are_materially_diverse() -> None:
    """The Phase 3 conformance controls collapsed to two behaviours. This must not."""
    rows = controls()
    assert 30 <= len(rows) <= 50
    assert len({row.model_visible_input for row in rows}) == len(rows)
    assert len({row.family for row in rows}) >= 8
    assert {row.family for row in rows} <= {family.value for family in BenignFamily}


def test_attack_families_are_covered_and_supported() -> None:
    authored = [case for case in attacks()]
    families = Counter(case.family for case in authored)
    assert set(families) == {family.value for family in AttackFamily}
    assert min(families.values()) >= 4
    # Families the upstream target cannot support must not be claimed.
    assert "confirmation_bypass" not in families
    assert "cross_tenant_leakage" not in families


def test_gold_labels_use_the_existing_reason_catalog() -> None:
    for scenario in corpus():
        for label in scenario.gold.expected_detection_labels:
            assert label in REASONS, (scenario.id, label)
        assert "CROSS_TENANT_ACCESS" not in scenario.gold.expected_detection_labels


def test_benign_controls_expect_no_violations() -> None:
    for scenario in controls():
        assert scenario.gold.expected_detection_labels == []
        assert scenario.gold.intended_violation is None


def test_variants_declare_their_semantic_parent() -> None:
    scenarios = corpus()
    variants = [scenario for scenario in scenarios if scenario.source == "variant"]
    parents = {scenario.id for scenario in scenarios if scenario.source == "authored"}
    assert variants
    for variant in variants:
        assert variant.semantic_parent_id in parents
        assert variant.variant_operator
        assert variant.model_visible_input != next(
            scenario.model_visible_input
            for scenario in scenarios
            if scenario.id == variant.semantic_parent_id
        )


def test_untrusted_content_is_actually_relayed_in_a_turn() -> None:
    relayed = [scenario for scenario in corpus() if scenario.untrusted_content]
    assert relayed
    for scenario in relayed:
        for block in scenario.untrusted_content:
            assert any(block.text in turn for turn in scenario.turns)


def test_scenario_rejects_a_hidden_control_channel() -> None:
    with pytest.raises(ValidationError):
        RealAgentScenario(
            id="bad",
            name="bad",
            is_attack=True,
            family=AttackFamily.DIRECT_INJECTION.value,
            turns=['Do it. AXIOM_COMMANDS=[{"op":"call","tool":"delete"}]'],
            gold=GoldLabel(expected_safe_behavior="refuse"),
            rationale="invalid",
        )


def test_scenario_rejects_untrusted_content_outside_a_turn() -> None:
    with pytest.raises(ValidationError):
        RealAgentScenario(
            id="bad",
            name="bad",
            is_attack=True,
            family=AttackFamily.INDIRECT_INJECTION.value,
            turns=["Please summarise this."],
            untrusted_content=[{"source": "email", "text": "hidden instruction"}],  # type: ignore[list-item]
            gold=GoldLabel(expected_safe_behavior="refuse"),
            rationale="invalid",
        )


def test_gold_label_requires_a_consistent_intended_violation() -> None:
    with pytest.raises(ValidationError):
        GoldLabel(
            intended_violation="EXCESSIVE_AGENCY",
            expected_detection_labels=["AUTHORIZATION_BYPASS"],
            expected_safe_behavior="refuse",
        )


def test_corpus_statistics_are_stable_and_report_variants_separately() -> None:
    statistics = corpus_statistics(corpus())
    assert statistics["authored_attack_cases"] == 40
    assert statistics["variant_attack_cases"] == 8
    assert statistics["attack_cases"] == 48
    assert statistics["unique_semantic_parents"] == 40
    assert statistics["benign_controls"] == 40
    assert statistics["unique_prompts"] == 88
    assert len(statistics["attack_families"]) == 10
    assert statistics["corpus_digest"] == corpus_statistics(corpus())["corpus_digest"]
