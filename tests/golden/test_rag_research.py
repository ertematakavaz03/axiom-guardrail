from __future__ import annotations

from collections import Counter

from demos.rag_research.seed import SCENARIOS


def test_rag_golden_suite_has_required_coverage() -> None:
    assert len(SCENARIOS) >= 30
    assert len({scenario["name"] for scenario in SCENARIOS}) == len(SCENARIOS)
    categories = Counter(scenario["category"] for scenario in SCENARIOS)
    required = {
        "simple_factual",
        "semantic_paraphrase",
        "exact_keyword",
        "policy_id",
        "multi_document",
        "retrieval_failure",
        "wrong_document",
        "wrong_citation",
        "missing_citation",
        "unsupported_claim",
        "hallucinated_value",
        "stale_source",
        "conflicting_documents",
        "newer_version_wins",
        "role_restricted",
        "tenant_isolation",
        "retrieval_timeout",
        "multiple_gold",
        "partial_grounding",
        "citation_not_retrieved",
        "lexical_better",
        "dense_better",
        "hybrid_improves",
    }
    assert required <= set(categories)


def test_required_refund_failure_contract_is_exact() -> None:
    scenario = next(
        item for item in SCENARIOS if item["name"] == "Refund after 30 days unsupported claim"
    )

    assert scenario["input"] == "Can customers request a refund 30 days after the purchase?"
    assert scenario["gold"] == ["refund_current"]
    assert scenario["filters"] == {"version": "2"}
    assert scenario["behavior"] == "unsupported_claim"
    assert scenario["answer"] == "Customers can request refunds within 30 days."
    assert scenario["severity"] == "critical"


def test_required_root_cause_cases_exist() -> None:
    by_category = {item["category"]: item for item in SCENARIOS}

    assert by_category["retrieval_failure"]["gold"] == ["refund_current"]
    assert by_category["stale_source"]["filters"] == {"version": "1"}
    assert by_category["tenant_isolation"]["severity"] == "critical"
