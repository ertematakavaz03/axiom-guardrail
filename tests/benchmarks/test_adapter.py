from __future__ import annotations

import pytest
from adapter import (
    ExternalAgentError,
    HttpResult,
    LangGraphHttpAdapter,
    normalize_messages,
    parse_retrieval_result,
    redact,
)


def test_adapter_normalizes_and_links_tool_results() -> None:
    messages = normalize_messages(
        [
            {"id": "h1", "type": "human", "content": "status"},
            {
                "id": "a1",
                "type": "ai",
                "content": "",
                "tool_calls": [
                    {"id": "call-1", "name": "get_order_status", "args": {"order_id": "123456"}}
                ],
            },
            {
                "id": "t1",
                "type": "tool",
                "name": "get_order_status",
                "tool_call_id": "call-1",
                "status": "success",
                "content": "Order 123456 is in transit",
            },
        ]
    )
    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assert messages[1]["tool_calls"][0]["arguments"] == {"order_id": "123456"}


def test_retrieval_parser_maps_only_observed_content() -> None:
    tool_result = """
Query: 'return policy'
Categories: Return
Min Similarity: 0.00
┌─ Result #1
│  Relevance: High (0.812)
│  Category:  Return
│  Type:      Policy
├─
│  Return Policy:
│  - Time limit: 30 days
└─
"""
    parsed = parse_retrieval_result(
        tool_result, {"kb-return-policy": "Return Policy:", "other": "Shipping Options:"}
    )
    assert parsed["query"] == "return policy"
    assert parsed["items"][0]["document_id"] == "kb-return-policy"
    assert parsed["items"][0]["reported_similarity"] == 0.812
    assert parsed["document_ids_are_benchmark_derived"] is True


def test_secret_redaction_keeps_synthetic_canaries() -> None:
    value = {
        "Authorization": "Bearer real-secret",
        "input_tokens": 42,
        "text": "Bearer abc.def and AXIOM-CANARY-LGV1-001-K7Q9",
    }
    redacted = redact(value)
    assert redacted["Authorization"] == "[REDACTED]"
    assert "abc.def" not in redacted["text"]
    assert "AXIOM-CANARY-LGV1-001-K7Q9" in redacted["text"]
    assert redacted["input_tokens"] == 42


def test_redaction_preserves_synthetic_return_authorization() -> None:
    redacted = redact(
        {
            "header": "Authorization: Bearer real-secret",
            "tool_result": "Return Authorization: RMA-123456-789",
        }
    )
    assert "real-secret" not in redacted["header"]
    assert redacted["tool_result"] == "Return Authorization: RMA-123456-789"


def test_empty_langgraph_run_is_a_transient_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LangGraphHttpAdapter("http://127.0.0.1:8123")
    monkeypatch.setattr(
        adapter,
        "_request",
        lambda *_args, **_kwargs: HttpResult(
            body={},
            headers={"Content-Location": "/threads/thread-1/runs/run-1"},
            elapsed_ms=5,
        ),
    )

    with pytest.raises(ExternalAgentError, match="returned no messages") as error:
        adapter.run_turn("thread-1", "hello")

    assert error.value.transient is True
