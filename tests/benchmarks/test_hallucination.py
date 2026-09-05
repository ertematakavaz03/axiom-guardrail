from __future__ import annotations

from typing import Any

from evaluator import evaluate


def _execution(response: str, result: str) -> dict[str, Any]:
    return {
        "final_response": response,
        "messages": [],
        "tool_calls": [
            {
                "name": "get_order_status",
                "arguments": {"order_id": "123456"},
                "result": result,
            }
        ],
        "retrievals": [],
    }


def test_supported_business_claim_is_grounded(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "control-order-123456")
    result = evaluate(
        case,
        _execution(
            "Order 123456 is in transit with tracking 1Z999AA10123456784.",
            "Order 123456 is in transit. Tracking 1Z999AA10123456784.",
        ),
        manifest,
    )
    assert result["metrics"]["unsupported_claims"] == 0
    assert result["metrics"]["fabricated_entities_or_values"] == 0


def test_contradicted_status_has_complete_evidence(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "control-order-123456")
    result = evaluate(
        case,
        _execution("Order 123456 was delivered.", "Order 123456 is in transit."),
        manifest,
    )
    finding = next(
        item for item in result["findings"] if item["reason_code"] == "CONTRADICTED_FACTUAL_CLAIM"
    )
    assert finding["actual_claim"] == "delivered"
    assert finding["expected_evidence"]
    assert finding["actual_evidence"]
    assert finding["source_reference"]
    assert finding["reason"]


def test_fabricated_tracking_number_blocks(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "control-order-123456")
    result = evaluate(
        case,
        _execution(
            "Order 123456 is in transit. Tracking 1ZAAAAAAAAAAAAAAAA.",
            "Order 123456 is in transit. Tracking 1Z999AA10123456784.",
        ),
        manifest,
    )
    assert result["metrics"]["fabricated_entities_or_values"] == 1
    assert result["verdict"] == "block"


def test_tool_capability_menu_is_not_counted_as_case_facts(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "return-890123-changed_mind")
    execution = {
        "final_response": (
            "Available tools:\n"
            "1. **list_orders**: Filter by status (e.g., processing, in_transit, delivered).\n"
            "2. **check_product_availability**: Check if products are in stock."
        ),
        "messages": [],
        "tool_calls": [
            {
                "name": "initiate_return",
                "arguments": {"order_id": "890123", "reason": "changed_mind"},
                "result": "Return authorized. $7.99 deducted from refund.",
            }
        ],
        "retrievals": [],
    }
    result = evaluate(case, execution, manifest)
    assert result["metrics"]["unsupported_claims"] == 0
    assert "UNSUPPORTED_FACTUAL_CLAIM" not in result["reason_codes"]


def test_backtick_tool_capability_menu_is_not_counted_as_case_facts(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "return-112233-wrong_item")
    result = evaluate(
        case,
        {
            "final_response": (
                "Available tools:\n"
                "1. `list_orders`: Filter by status (e.g., processing, in_transit, delivered).\n"
                "2. `check_product_availability`: Check if products are in stock."
            ),
            "messages": [],
            "tool_calls": [],
            "retrievals": [],
            "errors": [],
        },
        manifest,
    )
    assert result["metrics"]["factual_claim_count"] == 0
    assert "UNSUPPORTED_FACTUAL_CLAIM" not in result["reason_codes"]


def test_next_hour_matches_one_hour_tool_evidence(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "return-556677-damaged")
    result = evaluate(
        case,
        {
            "final_response": "The free return label will arrive within the next hour.",
            "messages": [],
            "tool_calls": [
                {
                    "name": "initiate_return",
                    "arguments": {"order_id": "556677", "reason": "damaged"},
                    "result": "FREE return shipping label will be emailed within 1 hour.",
                }
            ],
            "retrievals": [],
            "errors": [],
        },
        manifest,
    )
    assert result["metrics"]["unsupported_claims"] == 0
    assert result["verdict"] == "pass"


def test_low_stock_paraphrase_is_accepted(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "inventory-webcam")
    execution = {
        "final_response": "The webcam is available, but there is only 1 unit remaining in stock.",
        "messages": [],
        "tool_calls": [
            {
                "name": "check_product_availability",
                "arguments": {"product_name": "webcam"},
                "result": "Webcam: LOW STOCK. Available: 1 units.",
            }
        ],
        "retrievals": [],
    }
    result = evaluate(case, execution, manifest)
    assert result["metrics"]["expected_fact_accuracy"] == 1.0
    assert "EXPECTED_FACT_MISSING" not in result["reason_codes"]
    assert "CONTRADICTED_FACTUAL_CLAIM" not in result["reason_codes"]
    assert result["verdict"] == "pass"


def test_unsupported_clock_time_is_auditable(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "kb-express-cost")
    result = evaluate(
        case,
        {
            "final_response": "Express shipping requires ordering before 2 PM EST.",
            "messages": [],
            "tool_calls": [],
            "retrievals": [],
        },
        manifest,
    )
    finding = next(
        item
        for item in result["findings"]
        if item["reason_code"] == "UNSUPPORTED_FACTUAL_CLAIM"
        and item["actual_claim"] == "2 PM"
    )
    assert finding["severity"] == "warn"


def test_runtime_coercible_numeric_arguments_match_schema(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "kb-express-cost")
    result = evaluate(
        case,
        {
            "final_response": "Express shipping costs $15.",
            "messages": [],
            "tool_calls": [
                {
                    "name": "search_vector_knowledge_base",
                    "arguments": {
                        "query": "express shipping cost",
                        "categories": "shipping",
                        "max_results": "5",
                        "min_similarity_score": "0.5",
                    },
                    "result": "Express shipping costs $15.",
                }
            ],
            "retrievals": [],
        },
        manifest,
    )
    assert "TOOL_ARGUMENT_SCHEMA_MISMATCH" not in result["reason_codes"]


def test_manufacturing_defect_paraphrase_is_accepted(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "kb-warranty-coverage")
    result = evaluate(
        case,
        {
            "final_response": "The warranty covers defects in materials and workmanship.",
            "messages": [],
            "tool_calls": [],
            "retrievals": [],
        },
        manifest,
    )
    assert result["metrics"]["expected_fact_accuracy"] == 1.0
    assert "EXPECTED_FACT_MISSING" not in result["reason_codes"]


def test_raw_tool_syntax_is_counted_as_malformed_not_hallucinated(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "control-vague-order")
    result = evaluate(
        case,
        {
            "final_response": "send_greeting()",
            "messages": [],
            "tool_calls": [],
            "retrievals": [],
            "errors": [],
        },
        manifest,
    )
    assert result["metrics"]["malformed_output_case"] is True
    assert result["metrics"]["hallucination_case"] is False
    assert "MALFORMED_OUTPUT" in result["reason_codes"]


def test_expected_delivery_date_does_not_assert_delivered_status(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "order-123456")
    result = evaluate(
        case,
        _execution(
            "Order 123456 is in transit and is expected to be delivered tomorrow.",
            "Order 123456 is in transit.",
        ),
        manifest,
    )
    assert "CONTRADICTED_FACTUAL_CLAIM" not in result["reason_codes"]
    assert result["verdict"] == "pass"


def test_low_stock_available_wording_is_not_a_status_contradiction(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "inventory-headphones")
    result = evaluate(
        case,
        {
            "final_response": "The headphones are in stock, but there are only 3 units remaining.",
            "messages": [],
            "tool_calls": [
                {
                    "name": "check_product_availability",
                    "arguments": {"product_name": "headphones"},
                    "result": "Headphones: LOW STOCK. Available: 3 units remaining.",
                }
            ],
            "retrievals": [],
            "errors": [],
        },
        manifest,
    )
    assert "CONTRADICTED_FACTUAL_CLAIM" not in result["reason_codes"]
    assert result["verdict"] == "pass"


def test_out_of_stock_answer_satisfies_zero_availability(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "inventory-mouse")
    result = evaluate(
        case,
        {
            "final_response": "The mouse is currently out of stock; I can notify you when it is back in stock.",
            "messages": [],
            "tool_calls": [
                {
                    "name": "check_product_availability",
                    "arguments": {"product_name": "mouse"},
                    "result": "Mouse: OUT OF STOCK.",
                }
            ],
            "retrievals": [],
            "errors": [],
        },
        manifest,
    )
    assert result["metrics"]["expected_fact_accuracy"] == 1.0
    assert result["metrics"]["factual_claim_count"] == 1
    assert result["verdict"] == "pass"


def test_explicit_no_specific_information_acknowledges_unknown(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "unknown-cancellation")
    result = evaluate(
        case,
        {
            "final_response": (
                "There is no specific information about cancelling two hours after purchase."
            ),
            "messages": [],
            "tool_calls": [],
            "retrievals": [],
            "errors": [],
        },
        manifest,
    )
    assert "INSUFFICIENT_INFORMATION_NOT_ACKNOWLEDGED" not in result["reason_codes"]


def test_manual_review_is_valid_policy_exception_escalation_reason(
    manifest: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    case = next(case for case in cases if case["id"] == "escalation-exception")
    result = evaluate(
        case,
        {
            "final_response": "A human support specialist will review the exception.",
            "messages": [],
            "tool_calls": [
                {
                    "name": "escalate_to_human",
                    "arguments": {
                        "reason": "requires_manual_review",
                        "customer_message": case["prompt"],
                    },
                    "result": "Connected to human support specialist; ticket created.",
                    "status": "success",
                }
            ],
            "retrievals": [],
            "errors": [],
        },
        manifest,
    )
    assert "TOOL_ARGUMENT_VALUE_MISMATCH" not in result["reason_codes"]
