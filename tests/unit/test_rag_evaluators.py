from __future__ import annotations

from packages.agent_sdk.contracts import (
    AgentCitation,
    AgentClaim,
    AgentExecutionResult,
    TokenUsage,
)
from services.evaluators.models import EvaluationContext
from services.evaluators.verdicts import aggregate_run, order_reason_codes
from services.rag.evaluators import CitationAndGroundednessEvaluator, RetrievalEvaluator
from services.rag.models import (
    GoldEvidenceRef,
    RetrievalHit,
    RetrievalResult,
    RetrievalScope,
)

SCOPE = RetrievalScope(organization_id="org-a", project_id="project-a", corpus_id="corpus-a")


def retrieval_hit(
    *,
    content: str = "Refund requests must be submitted within 14 days.",
    document_id: str = "refund-policy",
    version_id: str = "v2",
    chunk_id: str = "chunk-v2",
    scope: RetrievalScope = SCOPE,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        document_id=document_id,
        document_version_id=version_id,
        content=content,
        document_name="refund_policy_v2.md",
        rank=1,
        metadata=scope.model_dump(),
    )


def context(
    execution: AgentExecutionResult,
    hit: RetrievalHit,
    gold: GoldEvidenceRef | None = None,
) -> EvaluationContext:
    return EvaluationContext(
        scenario={"metadata": {"rag_enabled": True}},
        execution=execution,
        retrieval=RetrievalResult(query="refund", hits=[hit]),
        gold_evidence=[gold] if gold else [],
        rag_scope=SCOPE,
    )


def execution(answer: str, *, citation: bool = True) -> AgentExecutionResult:
    claims = [AgentClaim(id="claim_1", text=answer)]
    citations = (
        [
            AgentCitation(
                citation_id="c1",
                chunk_id="chunk-v2",
                document_id="refund-policy",
                claim_ids=["claim_1"],
            )
        ]
        if citation
        else []
    )
    return AgentExecutionResult(
        final_response=answer,
        citations=citations,
        claims=claims,
        token_usage=TokenUsage(),
        latency_ms=1,
    )


def test_supported_claim_passes_citation_and_groundedness() -> None:
    results = CitationAndGroundednessEvaluator().evaluate(
        context(execution("Refund requests must be submitted within 14 days."), retrieval_hit())
    )
    by_metric = {result.metric: result for result in results}

    assert by_metric["citation_exists"].passed
    assert by_metric["citation_support"].passed
    assert by_metric["groundedness"].passed
    assert by_metric["groundedness"].value == 1.0


def test_wrong_numeric_claim_is_unsupported_even_when_citation_exists() -> None:
    results = CitationAndGroundednessEvaluator().evaluate(
        context(execution("Customers can request refunds within 30 days."), retrieval_hit())
    )
    by_metric = {result.metric: result for result in results}

    assert by_metric["citation_exists"].passed
    assert by_metric["citation_support"].reason_code == "WRONG_CITATION"
    assert by_metric["groundedness"].reason_code == "UNSUPPORTED_CLAIM"
    assert by_metric["groundedness"].value == 0.0


def test_missing_citation_is_distinct_from_unsupported_cited_claim() -> None:
    results = CitationAndGroundednessEvaluator().evaluate(
        context(execution("Refund requests must be submitted within 14 days.", citation=False), retrieval_hit())
    )
    groundedness = next(result for result in results if result.metric == "groundedness")

    assert groundedness.reason_code == "MISSING_CITATION"


def test_stale_source_is_reported_with_expected_and_actual_version() -> None:
    old = retrieval_hit(
        content="Refund requests are accepted within 30 days.",
        version_id="v1",
        chunk_id="chunk-v1",
    )
    gold = GoldEvidenceRef(
        document_id="refund-policy", document_version_id="v2", chunk_id="chunk-v2"
    )
    results = RetrievalEvaluator().evaluate(context(execution(old.content), old, gold))
    recall = next(result for result in results if result.metric == "retrieval_recall_at_5")

    assert recall.reason_code == "STALE_SOURCE_USED"
    assert recall.evidence["expected_document_version_id"] == "v2"
    assert recall.evidence["actual_document_version_id"] == "v1"


def test_missing_gold_evidence_is_retrieval_root_cause() -> None:
    wrong = retrieval_hit(document_id="shipping", version_id="shipping-v1", chunk_id="shipping-1")
    gold = GoldEvidenceRef(document_id="refund-policy", chunk_id="refund-1")
    results = RetrievalEvaluator().evaluate(context(execution(wrong.content), wrong, gold))
    recall = next(result for result in results if result.metric == "retrieval_recall_at_5")

    assert recall.reason_code == "GOLD_EVIDENCE_NOT_RETRIEVED"


def test_cross_tenant_evidence_is_a_security_failure() -> None:
    other = RetrievalScope(organization_id="org-b", project_id="project-b", corpus_id="corpus-b")
    leaked = retrieval_hit(scope=other)
    results = RetrievalEvaluator().evaluate(context(execution(leaked.content), leaked))

    assert results[0].reason_code == "RAG_TENANT_SCOPE_VIOLATION"


def test_no_gold_evidence_is_na_not_zero() -> None:
    results = RetrievalEvaluator().evaluate(context(execution(retrieval_hit().content), retrieval_hit()))
    recall = next(result for result in results if result.metric == "retrieval_recall_at_5")

    assert recall.passed
    assert recall.value is None


def test_rag_reason_precedence_keeps_root_cause_primary() -> None:
    ordered = order_reason_codes(
        ["WRONG_CITATION", "UNSUPPORTED_CLAIM", "GOLD_EVIDENCE_NOT_RETRIEVED", "QDRANT_TIMEOUT"]
    )

    assert ordered == [
        "QDRANT_TIMEOUT",
        "GOLD_EVIDENCE_NOT_RETRIEVED",
        "UNSUPPORTED_CLAIM",
        "WRONG_CITATION",
    ]


def test_rag_aggregation_excludes_na_metrics() -> None:
    metrics, score, verdict = aggregate_run(
        [
            {
                "verdict": "pass",
                "reason_codes": [],
                "evaluations": [
                    {"case_id": "1", "metric": "task_success", "passed": True, "value": 1.0},
                    {"case_id": "1", "metric": "groundedness", "passed": True, "value": 1.0},
                    {"case_id": "1", "metric": "ndcg", "passed": True, "value": None},
                    {"case_id": "1", "metric": "citation_exists", "passed": True, "value": 1.0},
                    {"case_id": "1", "metric": "citation_support", "passed": True, "value": 1.0},
                ],
            }
        ]
    )

    assert metrics["ndcg"] is None
    assert metrics["groundedness"] == 1.0
    assert score == 100.0
    assert verdict == "pass"
