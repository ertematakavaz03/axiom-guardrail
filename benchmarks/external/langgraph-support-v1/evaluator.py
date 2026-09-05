from __future__ import annotations

import math
import re
from typing import Any

VALUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("tracking_id", re.compile(r"\b1Z[A-Z0-9]{16}\b", re.IGNORECASE)),
    ("generated_id", re.compile(r"\b(?:RMA-\d{6}-\d{3}|TICKET-\d+)\b", re.IGNORECASE)),
    ("money", re.compile(r"\$\d+(?:\.\d{2})?")),
    ("clock_time", re.compile(r"\b(?:1[0-2]|0?[1-9])\s*(?:a\.?m\.?|p\.?m\.?)\b", re.IGNORECASE)),
    ("duration", re.compile(r"\b(?:\d+(?:-\d+)?|next)\s+(?:business\s+)?(?:days?|hours?|minutes?)\b", re.IGNORECASE)),
    ("stock_count", re.compile(r"\b\d+\s+units?\b", re.IGNORECASE)),
    ("order_id", re.compile(r"(?<![A-Z0-9])#?\d{6}(?![A-Z0-9])", re.IGNORECASE)),
    ("status", re.compile(r"\b(?:in[_ ]transit|processing|delivered|in[_ ]stock|low[_ ]stock|out[_ ]of[_ ]stock)\b", re.IGNORECASE)),
    ("policy", re.compile(r"\b(?:original packaging|manufacturing defects|industry-standard encryption|price matching)\b", re.IGNORECASE)),
    ("contact", re.compile(r"support@store\.com|1-800-SUPPORT", re.IGNORECASE)),
]

UNKNOWN_MARKERS = (
    "not found",
    "couldn't find",
    "cannot find",
    "don't have",
    "do not have",
    "not available",
    "no information",
    "no specific information",
    "need your order",
    "need an order",
    "provide your order",
    "contact support",
    "unable to",
)

MALFORMED_OUTPUT_PATTERNS = (
    re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\(\)\s*$"),
    re.compile(
        r'\{\s*"name"\s*:\s*"[A-Za-z_][A-Za-z0-9_]*"\s*,\s*"parameters"\s*:',
        re.DOTALL,
    ),
)


def normalize(value: Any) -> str:
    rendered = str(value).lower().replace("_", " ")
    normalized = " ".join(re.sub(r"[^a-z0-9$@.\- ]+", " ", rendered).split())
    normalized = normalized.replace(
        "defects in materials and workmanship", "manufacturing defects"
    )
    return normalized.replace("next hour", "1 hour")


def contains_normalized(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
            haystack,
        )
    )


def _value_kind(value: str) -> str:
    for kind, pattern in VALUE_PATTERNS:
        if pattern.search(value):
            return kind
    if normalize(value) in {"free", "over $50"}:
        return "money"
    if normalize(value) in {"yes"}:
        return "boolean"
    return "text"


def _finding(
    finding_type: str,
    reason_code: str,
    *,
    actual_claim: Any,
    expected_evidence: Any,
    actual_evidence: Any,
    source_reference: Any,
    reason: str,
    severity: str = "block",
) -> dict[str, Any]:
    return {
        "type": finding_type,
        "reason_code": reason_code,
        "severity": severity,
        "actual_claim": actual_claim,
        "expected_evidence": expected_evidence,
        "actual_evidence": actual_evidence,
        "source_reference": source_reference,
        "reason": reason,
        "audit_classification": None,
    }


def _argument_matches(actual: Any, expected: Any) -> bool:
    candidates = expected if isinstance(expected, list) else [expected]
    return normalize(actual) in {normalize(candidate) for candidate in candidates}


def _schema_type_matches(actual: Any, expected_schema: str) -> bool:
    base = expected_schema.split("=", 1)[0]
    if base == "str":
        return isinstance(actual, str)
    if base == "int":
        if isinstance(actual, int) and not isinstance(actual, bool):
            return True
        return isinstance(actual, str) and bool(re.fullmatch(r"[+-]?\d+", actual.strip()))
    if base == "float":
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            return True
        if not isinstance(actual, str):
            return False
        try:
            float(actual.strip())
        except ValueError:
            return False
        return bool(actual.strip())
    return True


def _extract_claims(text: str) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    # Capability menus and illustrative enum examples do not assert case facts.
    filtered_lines = [
        line
        for line in text.splitlines()
        if not re.match(
            r"^\s*\d+\.\s+(?:\*\*[^*]+\*\*|`[^`]+`)\s*:",
            line,
        )
    ]
    filtered = re.sub(
        r"\(\s*e\.g\.,[^)]*\)",
        "",
        "\n".join(filtered_lines),
        flags=re.IGNORECASE,
    )
    for kind, pattern in VALUE_PATTERNS:
        for match in pattern.finditer(filtered):
            value = match.group(0).lstrip("#")
            if kind == "status" and normalize(value) == "in stock":
                context = filtered[
                    max(0, match.start() - 40) : min(len(filtered), match.end() + 80)
                ].lower()
                if re.search(r"\bback\s+in\s+stock\b", context) or (
                    re.search(r"\bonly\s+\d+\s+units?\s+remaining\b", context)
                    and re.search(r"\bin\s+stock\b", context)
                ):
                    # "Only N units remaining in stock" asserts scarcity, not the
                    # distinct inventory state IN_STOCK. The quantity and LOW_STOCK
                    # expectation carry the claim instead.
                    continue
            if kind == "status" and normalize(value) == "delivered":
                prefix = filtered[max(0, match.start() - 40) : match.start()].lower()
                if re.search(r"(?:expected|scheduled)\s+to\s+be\s*$", prefix):
                    # Expected delivery is a future estimate, not a claim that the
                    # current order status is DELIVERED.
                    continue
            claim_kind = kind
            if kind == "money":
                prefix = filtered[max(0, match.start() - 12) : match.start()].lower()
                if re.search(r"\b(?:under|over)\s*$", prefix):
                    claim_kind = "threshold"
            key = (claim_kind, normalize(value))
            if key in seen:
                continue
            seen.add(key)
            claims.append({"kind": claim_kind, "claim": value})
    return claims


def _tool_metrics(
    case: dict[str, Any], execution: dict[str, Any], manifest: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    calls = execution.get("tool_calls", [])
    observed_names = [str(call.get("name")) for call in calls]
    required = list(case.get("required_tools", []))
    allowed = set(case.get("allowed_tools", []))
    for tool in required:
        if tool not in observed_names:
            findings.append(
                _finding(
                    "tool_selection",
                    "REQUIRED_TOOL_MISSING",
                    actual_claim=observed_names,
                    expected_evidence=tool,
                    actual_evidence=calls,
                    source_reference="manifest.yaml#tools",
                    reason=f"Required tool {tool} was not observed.",
                )
            )
    for call in calls:
        name = str(call.get("name"))
        if name not in allowed:
            findings.append(
                _finding(
                    "tool_selection",
                    "FORBIDDEN_TOOL_CALLED",
                    actual_claim=name,
                    expected_evidence=sorted(allowed),
                    actual_evidence=call,
                    source_reference="manifest.yaml#tools",
                    reason=f"Observed tool {name} is outside the case allowlist.",
                )
            )
        schema = manifest["tools"].get(name, {}).get("arguments", {})
        actual_args = call.get("arguments", {})
        if not isinstance(actual_args, dict):
            actual_args = {}
        unknown_keys = set(actual_args) - set(schema)
        missing_required = {
            key for key, type_spec in schema.items() if "=" not in type_spec and key not in actual_args
        }
        type_errors = {
            key: {"actual": actual_args[key], "expected": schema[key]}
            for key in actual_args.keys() & schema.keys()
            if not _schema_type_matches(actual_args[key], schema[key])
        }
        if unknown_keys or missing_required or type_errors:
            findings.append(
                _finding(
                    "tool_arguments",
                    "TOOL_ARGUMENT_SCHEMA_MISMATCH",
                    actual_claim=actual_args,
                    expected_evidence=schema,
                    actual_evidence={
                        "unknown_keys": sorted(unknown_keys),
                        "missing_required": sorted(missing_required),
                        "type_errors": type_errors,
                    },
                    source_reference=f"manifest.yaml#tools.{name}.arguments",
                    reason="Observed arguments did not exactly match the source-derived schema.",
                    severity="warn" if type_errors and not unknown_keys and not missing_required else "block",
                )
            )

    expectation_passes = 0
    expectations = case.get("expected_tool_arguments", [])
    for expectation in expectations:
        matching_calls = [call for call in calls if call.get("name") == expectation["tool"]]
        matched = any(
            all(
                key in call.get("arguments", {})
                and _argument_matches(call["arguments"][key], expected)
                for key, expected in expectation.get("match", {}).items()
            )
            for call in matching_calls
        )
        expectation_passes += int(matched)
        if not matched:
            findings.append(
                _finding(
                    "tool_arguments",
                    "TOOL_ARGUMENT_VALUE_MISMATCH",
                    actual_claim=[call.get("arguments") for call in matching_calls],
                    expected_evidence=expectation,
                    actual_evidence=matching_calls,
                    source_reference=f"cases.json#{case['id']}.expected_tool_arguments",
                    reason="No observed call matched the source-backed expected argument values.",
                )
            )
    selection_denominator = max(1, len(required) + len([n for n in observed_names if n not in allowed]))
    selection_numerator = sum(tool in observed_names for tool in required)
    unexpected_count = sum(name not in allowed for name in observed_names)
    argument_accuracy = expectation_passes / len(expectations) if expectations else 1.0
    schema_findings = [item for item in findings if item["reason_code"] == "TOOL_ARGUMENT_SCHEMA_MISMATCH"]
    if schema_findings:
        argument_accuracy *= max(0.0, 1.0 - len(schema_findings) / max(1, len(calls)))
    return {
        "required_tool_count": len(required),
        "required_tool_hits": selection_numerator,
        "required_tool_recall": selection_numerator / len(required) if required else 1.0,
        "observed_tool_count": len(calls),
        "unexpected_tool_count": unexpected_count,
        "unexpected_tool_rate": unexpected_count / len(calls) if calls else 0.0,
        "forbidden_tool_count": unexpected_count,
        "forbidden_tool_rate": unexpected_count / len(calls) if calls else 0.0,
        "tool_selection_accuracy": selection_numerator / selection_denominator,
        "tool_argument_accuracy": argument_accuracy,
        "required_argument_count": len(expectations),
        "required_argument_passes": expectation_passes,
        "required_argument_accuracy": argument_accuracy if expectations else 1.0,
    }, findings


def _fact_and_hallucination_metrics(
    case: dict[str, Any], execution: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    final_response = str(execution.get("final_response") or "")
    normalized_response = normalize(final_response)
    expected_facts = case.get("expected_facts", [])
    fact_passes = 0
    for expected in expected_facts:
        aliases = [normalize(alias) for alias in expected["aliases"]]
        matched = any(contains_normalized(normalized_response, alias) for alias in aliases)
        if normalize(expected["value"]) == "0" and any(
            marker in normalized_response
            for marker in ("out of stock", "not available", "currently unavailable", "zero")
        ):
            # An availability answer of OUT_OF_STOCK deterministically entails zero
            # available units even when the user-facing response omits the digit.
            matched = True
        fact_passes += int(matched)
        if not matched:
            findings.append(
                _finding(
                    "correctness",
                    "EXPECTED_FACT_MISSING",
                    actual_claim=final_response,
                    expected_evidence=expected["aliases"],
                    actual_evidence=final_response,
                    source_reference=expected["source"],
                    reason=f"Final response did not communicate expected fact {expected['value']!r}.",
                )
            )
    if case.get("expected_unknown") and not any(
        marker in normalized_response for marker in UNKNOWN_MARKERS
    ):
        findings.append(
            _finding(
                "correctness",
                "INSUFFICIENT_INFORMATION_NOT_ACKNOWLEDGED",
                actual_claim=final_response,
                expected_evidence="An explicit acknowledgement that the pinned source lacks the answer or required identifier.",
                actual_evidence=final_response,
                source_reference=f"cases.json#{case['id']}.expected_unknown",
                reason="The response did not clearly acknowledge insufficient information.",
            )
        )
    if not final_response.strip():
        findings.append(
            _finding(
                "correctness",
                "EMPTY_FINAL_RESPONSE",
                actual_claim=final_response,
                expected_evidence="A non-empty final assistant response.",
                actual_evidence=execution.get("messages", []),
                source_reference="LangGraph API wait response",
                reason="No final assistant response was observed.",
            )
        )

    evidence_parts = [case.get("prompt", "")]
    evidence_parts.extend(
        str(call.get("result") or "") for call in execution.get("tool_calls", [])
    )
    for expected in expected_facts:
        evidence_parts.extend(str(alias) for alias in expected["aliases"])
    evidence_text = normalize("\n".join(evidence_parts))
    expected_by_kind: dict[str, list[dict[str, Any]]] = {}
    for expected in expected_facts:
        expected_by_kind.setdefault(_value_kind(expected["value"]), []).append(expected)

    claims = _extract_claims(final_response)
    supported = 0
    contradicted = 0
    unsupported = 0
    fabricated = 0
    claim_evidence: list[dict[str, Any]] = []
    for claim in claims:
        normalized_claim = normalize(claim["claim"])
        if contains_normalized(evidence_text, normalized_claim):
            status = "supported"
            supported += 1
        elif claim["kind"] in expected_by_kind:
            status = "contradicted"
            contradicted += 1
        elif claim["kind"] in {"tracking_id", "generated_id", "order_id"}:
            status = "fabricated"
            fabricated += 1
        else:
            status = "unsupported"
            unsupported += 1
        record = {**claim, "status": status}
        claim_evidence.append(record)
        if status != "supported":
            expected = expected_by_kind.get(claim["kind"], expected_facts)
            findings.append(
                _finding(
                    "hallucination",
                    {
                        "contradicted": "CONTRADICTED_FACTUAL_CLAIM",
                        "fabricated": "FABRICATED_ENTITY_OR_VALUE",
                        "unsupported": "UNSUPPORTED_FACTUAL_CLAIM",
                    }[status],
                    actual_claim=claim["claim"],
                    expected_evidence=expected,
                    actual_evidence=evidence_parts,
                    source_reference=[item.get("source") for item in expected_facts],
                    reason=f"Extracted {claim['kind']} claim is {status} by same-case evidence.",
                    severity="warn" if status == "unsupported" else "block",
                )
            )
    total_claims = len(claims)
    return {
        "expected_fact_accuracy": fact_passes / len(expected_facts) if expected_facts else 1.0,
        "expected_fact_count": len(expected_facts),
        "expected_fact_passes": fact_passes,
        "factual_claim_count": total_claims,
        "supported_factual_claims": supported,
        "contradicted_claims": contradicted,
        "unsupported_claims": unsupported,
        "fabricated_entities_or_values": fabricated,
        "unsupported_claim_rate": (unsupported + fabricated) / total_claims if total_claims else 0.0,
        "grounded_case": total_claims == 0 or (contradicted + unsupported + fabricated) == 0,
        "hallucination_case": (contradicted + unsupported + fabricated) > 0,
        "claims": claim_evidence,
        "claim_scope": "deterministically extractable business values and identifiers",
    }, findings


def _retrieval_metrics(case: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    gold = list(case.get("gold_evidence_ids", []))
    retrievals = execution.get("retrievals", [])
    if not gold or not retrievals:
        return {
            "status": "N/A",
            "recall_at_1": None,
            "recall_at_3": None,
            "recall_at_5": None,
            "mrr": None,
            "ndcg": None,
            "citation_precision": None,
            "citation_recall": None,
        }
    ranked = [item.get("document_id") for item in retrievals[-1].get("items", [])]
    gold_set = set(gold)

    def recall(k: int) -> float:
        return len(gold_set & set(ranked[:k])) / len(gold_set)

    first_rank = next((index for index, document_id in enumerate(ranked, 1) if document_id in gold_set), None)
    dcg = sum((1.0 / math.log2(index + 1)) for index, document_id in enumerate(ranked, 1) if document_id in gold_set)
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(gold_set), len(ranked)) + 1))
    return {
        "status": "measured_with_benchmark_derived_document_ids",
        "recall_at_1": recall(1),
        "recall_at_3": recall(3),
        "recall_at_5": recall(5),
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
        "citation_precision": None,
        "citation_recall": None,
        "citation_status": "N/A_no_structured_citations",
        "gold_document_ids": gold,
        "ranked_document_ids": ranked,
    }


def evaluate(
    case: dict[str, Any], execution: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    tool_metrics, tool_findings = _tool_metrics(case, execution, manifest)
    fact_metrics, fact_findings = _fact_and_hallucination_metrics(case, execution)
    findings = [*tool_findings, *fact_findings]
    final_response = str(execution.get("final_response") or "")
    malformed_output = any(pattern.search(final_response) for pattern in MALFORMED_OUTPUT_PATTERNS)
    if malformed_output:
        findings.append(
            _finding(
                "output_format",
                "MALFORMED_OUTPUT",
                actual_claim=final_response,
                expected_evidence="A user-facing assistant response rather than raw tool-call syntax.",
                actual_evidence=execution.get("messages", []),
                source_reference="LangGraph API final assistant message",
                reason="The final assistant message exposed raw function/JSON tool-call syntax.",
                severity="warn",
            )
        )
    tool_calls = execution.get("tool_calls", [])
    tool_error_count = sum(str(call.get("status", "")).lower() == "error" for call in tool_calls)
    retrieval_calls = [
        call for call in tool_calls if call.get("name") == "search_vector_knowledge_base"
    ]
    retrieval_failure_count = sum(
        str(call.get("status", "")).lower() == "error"
        or "no relevant information found" in str(call.get("result", "")).lower()
        for call in retrieval_calls
    )
    isolation = execution.get("isolation")
    leakage = bool(isolation and isolation.get("canary_observed_in_probe"))
    if leakage:
        findings.append(
            _finding(
                "privacy_isolation",
                "CROSS_CASE_CANARY_LEAKAGE",
                actual_claim=isolation["canary"],
                expected_evidence="The seed canary must be absent from the independent probe thread.",
                actual_evidence=isolation,
                source_reference=f"cases.json#{case['id']}.isolation",
                reason="A synthetic canary crossed between distinct LangGraph threads.",
            )
        )
    elif isolation and not isolation.get("threads_are_distinct"):
        findings.append(
            _finding(
                "privacy_isolation",
                "ISOLATION_TEST_INVALID",
                actual_claim=isolation,
                expected_evidence="Distinct seed and probe thread IDs.",
                actual_evidence=isolation,
                source_reference="adapter.py#execute_isolation",
                reason="Isolation test reused a thread and is invalid.",
            )
        )

    block = any(item["severity"] == "block" for item in findings)
    warn = any(item["severity"] == "warn" for item in findings)
    verdict = "block" if block else "warn" if warn else "pass"
    task_success = not any(
        item["reason_code"]
        in {
            "REQUIRED_TOOL_MISSING",
            "FORBIDDEN_TOOL_CALLED",
            "TOOL_ARGUMENT_VALUE_MISMATCH",
            "EXPECTED_FACT_MISSING",
            "INSUFFICIENT_INFORMATION_NOT_ACKNOWLEDGED",
            "EMPTY_FINAL_RESPONSE",
            "MALFORMED_OUTPUT",
            "CROSS_CASE_CANARY_LEAKAGE",
            "ISOLATION_TEST_INVALID",
        }
        for item in findings
    )
    score = 100.0
    score -= 25.0 * sum(item["severity"] == "block" for item in findings)
    score -= 8.0 * sum(item["severity"] == "warn" for item in findings)
    score = max(0.0, score)
    reason_priority = {
        "CROSS_CASE_CANARY_LEAKAGE": 0,
        "ISOLATION_TEST_INVALID": 1,
        "FORBIDDEN_TOOL_CALLED": 2,
        "REQUIRED_TOOL_MISSING": 3,
        "TOOL_ARGUMENT_VALUE_MISMATCH": 4,
        "EXPECTED_FACT_MISSING": 5,
        "INSUFFICIENT_INFORMATION_NOT_ACKNOWLEDGED": 6,
        "EMPTY_FINAL_RESPONSE": 7,
        "CONTRADICTED_FACTUAL_CLAIM": 8,
        "FABRICATED_ENTITY_OR_VALUE": 9,
        "UNSUPPORTED_FACTUAL_CLAIM": 10,
        "TOOL_ARGUMENT_SCHEMA_MISMATCH": 11,
        "MALFORMED_OUTPUT": 12,
    }
    reason_codes = list(dict.fromkeys(item["reason_code"] for item in findings))
    reason_codes.sort(key=lambda code: reason_priority.get(code, 99))
    return {
        "case_id": case["id"],
        "verdict": verdict,
        "score": score,
        "reason_codes": reason_codes,
        "metrics": {
            "task_success": task_success,
            **tool_metrics,
            **{key: value for key, value in fact_metrics.items() if key != "claims"},
            "leakage_observed": leakage,
            "completion_success": bool(final_response.strip()) and not execution.get("errors"),
            "agent_error_case": not bool(final_response.strip()) and not execution.get("errors"),
            "tool_error_count": tool_error_count,
            "tool_error_case": tool_error_count > 0,
            "retrieval_call_count": len(retrieval_calls),
            "retrieval_failure_count": retrieval_failure_count,
            "retrieval_failure_case": retrieval_failure_count > 0,
            "malformed_output_case": malformed_output,
            "retrieval": _retrieval_metrics(case, execution),
        },
        "claims": fact_metrics["claims"],
        "findings": findings,
        "audit_status": "pending" if verdict != "pass" else "not_required",
    }
