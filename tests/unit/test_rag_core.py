from __future__ import annotations

import math
import uuid

import pytest

from packages.agent_sdk.contracts import AgentExecutionRequest
from services.orchestrator.adapters import DemoRagAgentAdapter
from services.rag.chunking import ChunkingConfig, chunk_sections, parse_document
from services.rag.embeddings import DeterministicEmbeddingProvider
from services.rag.errors import DocumentParsingError
from services.rag.metrics import normalized_dcg, recall_at_k, reciprocal_rank
from services.rag.models import GoldEvidenceRef, RetrievalHit, RetrievalScope
from services.rag.reranking import TokenOverlapReranker
from services.rag.retrieval import reciprocal_rank_fusion
from services.rag.sparse import lexical_overlap, sparse_vector
from services.rag.storage import QdrantVectorStore


def hit(
    chunk_id: str,
    *,
    document_id: str = "doc",
    version_id: str = "v1",
    content: str = "refund requests are allowed within 14 days",
    rank: int = 1,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        document_id=document_id,
        document_version_id=version_id,
        content=content,
        rank=rank,
    )


def test_text_and_markdown_chunking_is_configurable_and_stable() -> None:
    sections = parse_document(b"# Refunds\n" + b"policy " * 25, "markdown", "text/markdown")
    chunks = chunk_sections(sections, ChunkingConfig(chunk_size_tokens=10, overlap_tokens=2))

    assert len(chunks) == 3
    assert chunks[0].section_title == "Refunds"
    assert chunks[0].token_count == 10
    assert chunks[1].text.split()[:2] == chunks[0].text.split()[-2:]


def test_pdf_without_extractable_text_fails_structurally() -> None:
    with pytest.raises(DocumentParsingError) as error:
        parse_document(b"%PDF-not-a-real-document", "pdf", "application/pdf")
    assert error.value.reason_code == "DOCUMENT_PARSING_ERROR"


@pytest.mark.asyncio
async def test_deterministic_embeddings_are_repeatable_and_normalized() -> None:
    provider = DeterministicEmbeddingProvider(vector_size=32)
    first, second = await provider.embed_documents(["Policy ID RF-14", "Policy ID RF-14"])

    assert first == second
    assert math.isclose(sum(value * value for value in first), 1.0)


def test_sparse_vector_and_lexical_overlap_preserve_exact_terms() -> None:
    first = sparse_vector("Policy RF-14 refund refund")
    second = sparse_vector("Policy RF-14 refund refund")

    assert first == second
    assert lexical_overlap("RF-14 refund", "Refund policy RF-14") == 1.0


def test_rrf_merges_dense_and_sparse_provenance() -> None:
    dense = [hit("a", rank=1), hit("b", rank=2)]
    dense[0].dense_score = 0.9
    dense[1].dense_score = 0.8
    sparse = [hit("b", rank=1), hit("c", rank=2)]
    sparse[0].sparse_score = 5.0
    sparse[1].sparse_score = 4.0

    fused = reciprocal_rank_fusion(dense, sparse)

    assert [item.chunk_id for item in fused] == ["b", "a", "c"]
    assert fused[0].dense_score == 0.8
    assert fused[0].sparse_score == 5.0
    assert fused[0].hybrid_score is not None


@pytest.mark.asyncio
async def test_deterministic_reranker_prefers_query_overlap() -> None:
    candidates = [
        hit("shipping", content="international shipping takes five days"),
        hit("refund", content="refund requests must be submitted within 14 days"),
    ]
    reranked = await TokenOverlapReranker().rerank("refund within 14 days", candidates, 2)

    assert reranked[0].chunk_id == "refund"
    assert reranked[0].rank == 1


def test_qdrant_filter_always_contains_tenant_project_and_corpus() -> None:
    scope = RetrievalScope(organization_id="org-a", project_id="project-a", corpus_id="corpus-a")
    query_filter = QdrantVectorStore.build_filter(scope, {"trust_level": "trusted"})
    keys = {condition.key for condition in query_filter.must or [] if hasattr(condition, "key")}

    assert {"organization_id", "project_id", "corpus_id", "trust_level"} <= keys


def test_qdrant_filter_rejects_arbitrary_expressions() -> None:
    scope = RetrievalScope(organization_id="org", project_id="project", corpus_id="corpus")
    with pytest.raises(ValueError, match="Unsupported metadata filters"):
        QdrantVectorStore.build_filter(scope, {"$where": "true"})


def test_retrieval_metrics_support_recall_mrr_and_graded_ndcg() -> None:
    hits = [hit("noise"), hit("gold-2"), hit("gold-1")]
    gold = [
        GoldEvidenceRef(chunk_id="gold-1", relevance_score=3.0),
        GoldEvidenceRef(chunk_id="gold-2", relevance_score=1.0),
    ]

    assert recall_at_k(hits, gold, 1) == 0.0
    assert recall_at_k(hits, gold, 3) == 1.0
    assert reciprocal_rank(hits, gold) == 0.5
    assert normalized_dcg(hits, gold) is not None


def test_retrieval_metrics_are_na_without_gold_evidence() -> None:
    assert recall_at_k([hit("a")], [], 5) is None
    assert reciprocal_rank([hit("a")], []) is None
    assert normalized_dcg([hit("a")], []) is None


@pytest.mark.asyncio
async def test_demo_wrong_citation_selects_retrieved_non_gold_evidence() -> None:
    result = await DemoRagAgentAdapter().execute(
        AgentExecutionRequest(
            run_id=uuid.uuid4(),
            case_id=uuid.uuid4(),
            scenario={
                "input": "What is the refund deadline?",
                "metadata": {
                    "demo_behavior": "wrong_citation",
                    "demo_answer": "Refund requests must be submitted within 14 days.",
                },
                "gold_evidence": [{"chunk_id": "gold-refund"}],
            },
            agent_config={},
            sandbox_context={
                "retrieved_evidence": [
                    {
                        "chunk_id": "wrong-employee",
                        "document_id": "employee",
                        "content": "Employees receive 20 days of annual leave.",
                    },
                    {
                        "chunk_id": "gold-refund",
                        "document_id": "refund",
                        "content": "Refund requests must be submitted within 14 days.",
                    },
                ]
            },
        )
    )

    assert result.citations[0].chunk_id == "wrong-employee"
