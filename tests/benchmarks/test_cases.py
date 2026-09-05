from __future__ import annotations

from collections import Counter
from typing import Any


def test_case_ids_and_category_distribution(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    assert len({case["id"] for case in cases}) == len(cases)
    assert Counter(case["category"] for case in cases) == Counter(manifest["categories"])


def test_concurrent_subset_is_read_only(cases: list[dict[str, Any]]) -> None:
    selected = [case for case in cases if case["performance"]]
    assert len(selected) == 20
    assert all(case["read_only"] for case in selected)
    assert all(not case["isolation"] for case in selected)


def test_thread_reuse_is_explicit(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        if len(case["turns"]) > 1:
            assert case["category"] == "multi_turn"
        if case["isolation"]:
            assert case["category"] == "privacy_isolation"
