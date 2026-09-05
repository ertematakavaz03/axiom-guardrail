from __future__ import annotations

from typing import Any

from validator import validate


def test_manifest_and_generated_cases_are_valid(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    report = validate(manifest, cases)
    assert report["valid"], report["errors"]
    assert report["case_count"] == 100
    assert report["smoke_count"] == 12
    assert report["performance_count"] == 20
    assert report["stability_count"] == 20


def test_source_declares_exact_eight_bound_tools(manifest: dict[str, Any]) -> None:
    assert set(manifest["tools"]) == {
        "list_available_functions",
        "send_greeting",
        "search_vector_knowledge_base",
        "get_order_status",
        "list_orders",
        "initiate_return",
        "check_product_availability",
        "escalate_to_human",
    }
