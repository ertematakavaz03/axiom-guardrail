from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from generate_cases import generate

ROOT = Path(__file__).resolve().parent
SOURCE_PREFIXES = ("data/knowledge_base.json#", "src/support_agent/tools.py#")
SOURCE_BACKED_VALUES = {
    "30 days",
    "original packaging",
    "5-7 business days",
    "$7.99",
    "over $50",
    "2-3 business days",
    "$15",
    "next business day",
    "$25",
    "manufacturing defects",
    "yes",
    "stripe",
    "industry-standard encryption",
    "processing",
    "in transit",
    "delivered",
    "in stock",
    "low stock",
    "out of stock",
    "free",
    "human",
    "not found",
    "couldn't find",
    "0",
    "1",
    "3",
    "8",
    "15",
    "25",
    "1z999aa10123456784",
    "1z888bb20234567895",
    "1z666dd40456789017",
    "1z555ee50567890128",
    "1z444ff60678901239",
    "1z333gg70789012340",
    "visa",
    "paypal",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(manifest: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    known_tools = set(manifest["tools"])
    known_categories = set(manifest["categories"])
    known_kb = set(manifest["valid_kb_ids"])
    valid_orders = set(manifest["valid_order_ids"])
    valid_products = set(manifest["valid_products"])
    valid_reasons = set(manifest["valid_escalation_reasons"])
    canary_re = re.compile(manifest["canary_pattern"])

    ids = [str(case.get("id", "")) for case in cases]
    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate case IDs: {duplicates}")
    if len(cases) != manifest["primary_case_target"]:
        errors.append(f"expected {manifest['primary_case_target']} cases, found {len(cases)}")
    if cases != generate():
        errors.append("cases.json differs from deterministic generate_cases.py output")

    category_counts = Counter(str(case.get("category")) for case in cases)
    if dict(category_counts) != manifest["categories"]:
        errors.append(
            f"category counts differ: expected {manifest['categories']}, found {dict(category_counts)}"
        )
    canaries: list[str] = []

    for case in cases:
        case_id = str(case.get("id", "<missing>"))
        prefix = f"{case_id}: "
        category = case.get("category")
        if category not in known_categories:
            errors.append(prefix + f"unknown category {category!r}")
        if case.get("difficulty") not in manifest["difficulties"]:
            errors.append(prefix + f"invalid difficulty {case.get('difficulty')!r}")
        if not str(case.get("prompt", "")).strip():
            errors.append(prefix + "empty prompt")
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns or not all(isinstance(x, str) and x for x in turns):
            errors.append(prefix + "turns must be a non-empty list of strings")
        if category == "multi_turn" and len(turns or []) < 2:
            errors.append(prefix + "multi-turn case has fewer than two turns")
        if category != "multi_turn" and len(turns or []) > 1:
            errors.append(prefix + "only multi-turn cases may reuse one thread across turns")

        required = set(case.get("required_tools", []))
        allowed = set(case.get("allowed_tools", []))
        forbidden = set(case.get("forbidden_tools", []))
        for label, values in (("required", required), ("allowed", allowed), ("forbidden", forbidden)):
            unknown = values - known_tools
            if unknown:
                errors.append(prefix + f"unknown {label} tools: {sorted(unknown)}")
        if not required <= allowed:
            errors.append(prefix + "required tools are not a subset of allowed tools")
        if allowed & forbidden:
            errors.append(prefix + "allowed and forbidden tools overlap")
        if allowed | forbidden != known_tools:
            errors.append(prefix + "allowed/forbidden tools do not partition the bound tool set")

        for expectation in case.get("expected_tool_arguments", []):
            tool = expectation.get("tool")
            if tool not in known_tools:
                errors.append(prefix + f"argument expectation references unknown tool {tool!r}")
                continue
            schema_keys = set(manifest["tools"][tool]["arguments"])
            provided = set(expectation.get("match", {}))
            if not provided <= schema_keys:
                errors.append(prefix + f"invalid arguments for {tool}: {sorted(provided - schema_keys)}")
            values = expectation.get("match", {})
            order_id = values.get("order_id")
            if order_id and order_id not in valid_orders and category != "unknown":
                errors.append(prefix + f"non-upstream order ID {order_id!r}")
            product_values = values.get("product_name")
            if product_values and category != "unknown":
                candidates = product_values if isinstance(product_values, list) else [product_values]
                if not any(product in str(candidate).lower() for candidate in candidates for product in valid_products):
                    errors.append(prefix + f"product expectation lacks a valid upstream product: {candidates}")
            reason_values = values.get("reason")
            if reason_values:
                candidates = reason_values if isinstance(reason_values, list) else [reason_values]
                normalized = {str(item).replace(" ", "_") for item in candidates}
                if not normalized & valid_reasons and not normalized & {"defective", "damaged", "broken", "changed_mind", "wrong_item"}:
                    errors.append(prefix + f"invalid return/escalation reasons: {candidates}")

        for expected_fact in case.get("expected_facts", []):
            value = str(expected_fact.get("value", ""))
            source = str(expected_fact.get("source", ""))
            aliases = expected_fact.get("aliases")
            if not value or value.lower() not in SOURCE_BACKED_VALUES:
                errors.append(prefix + f"unsupported gold fact value {value!r}")
            if not source.startswith(SOURCE_PREFIXES):
                errors.append(prefix + f"invalid gold source {source!r}")
            if not isinstance(aliases, list) or not aliases:
                errors.append(prefix + f"fact {value!r} has no aliases")
        invalid_kb = set(case.get("gold_evidence_ids", [])) - known_kb
        if invalid_kb:
            errors.append(prefix + f"unknown gold KB IDs: {sorted(invalid_kb)}")

        isolation = case.get("isolation")
        if isolation:
            if category != "privacy_isolation":
                errors.append(prefix + "isolation fields are restricted to privacy cases")
            canary = str(isolation.get("canary", ""))
            if not canary_re.fullmatch(canary):
                errors.append(prefix + f"invalid synthetic canary {canary!r}")
            if canary not in str(isolation.get("seed_prompt", "")):
                errors.append(prefix + "seed prompt does not contain its canary")
            canaries.append(canary)
        elif category == "privacy_isolation":
            errors.append(prefix + "privacy case lacks isolation pair")

        if case.get("performance") and not case.get("read_only"):
            errors.append(prefix + "performance subset contains a simulated action case")

    if len(canaries) != len(set(canaries)):
        errors.append("synthetic canaries are not unique")
    if sum(bool(case.get("smoke")) for case in cases) != manifest["smoke"]["case_count"]:
        errors.append("smoke subset count is invalid")
    if sum(bool(case.get("stability")) for case in cases) != manifest["stability"]["case_count"]:
        errors.append("stability subset count is invalid")
    performance_cases = [case for case in cases if case.get("performance")]
    if len(performance_cases) != manifest["performance"]["case_count"]:
        errors.append("performance subset count is invalid")
    if any(not case.get("read_only") for case in performance_cases):
        errors.append("performance subset is not entirely read-only")

    if not errors and len(cases) < manifest["minimum_honest_cases"]:
        errors.append("validated case count is below the honest minimum")
    return {
        "valid": not errors,
        "benchmark_id": manifest["benchmark_id"],
        "case_count": len(cases),
        "category_counts": dict(sorted(category_counts.items())),
        "difficulty_counts": dict(sorted(Counter(case["difficulty"] for case in cases).items())),
        "smoke_count": sum(bool(case.get("smoke")) for case in cases),
        "performance_count": len(performance_cases),
        "stability_count": sum(bool(case.get("stability")) for case in cases),
        "canary_count": len(canaries),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the external LangGraph benchmark")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate(load_json(ROOT / "manifest.yaml"), load_json(ROOT / "cases.json"))
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
