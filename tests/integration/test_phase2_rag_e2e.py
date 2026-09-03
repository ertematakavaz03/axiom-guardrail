from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from apps.api.app.config import get_settings
from apps.api.app.db.models import Corpus, Document, DocumentVersion, GoldEvidence, Scenario
from apps.api.app.db.session import SessionLocal
from apps.api.app.workers.run_worker import execute_run
from demos.rag_research.seed import SCENARIOS as RAG_SCENARIOS
from demos.rag_research.seed import seed as seed_rag
from services.rag.embeddings import DeterministicEmbeddingProvider
from services.rag.models import RetrievalScope
from services.rag.sparse import sparse_vector
from services.rag.storage import QdrantVectorStore

pytestmark = pytest.mark.integration


async def register(client: AsyncClient, prefix: str) -> tuple[dict[str, str], dict[str, Any]]:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
            "password": "Integration123!",
            "organization_name": f"{prefix} organization",
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    project_response = await client.post(
        "/v1/projects",
        headers=headers,
        json={"name": f"{prefix} project", "description": "RAG integration"},
    )
    assert project_response.status_code == 201, project_response.text
    return headers, project_response.json()


async def create_corpus_and_config(
    client: AsyncClient, headers: dict[str, str], project_id: str, name: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    corpus_response = await client.post(
        f"/v1/projects/{project_id}/corpora",
        headers=headers,
        json={"name": name, "description": "Integration corpus", "version": "2"},
    )
    assert corpus_response.status_code == 201, corpus_response.text
    config_response = await client.post(
        f"/v1/projects/{project_id}/rag-configs",
        headers=headers,
        json={
            "name": "Hybrid deterministic",
            "embedding_provider": "deterministic",
            "embedding_model": "deterministic-hash-v1",
            "dense_enabled": True,
            "sparse_enabled": True,
            "top_k_dense": 10,
            "top_k_sparse": 10,
            "hybrid_top_k": 5,
            "reranker_type": "token_overlap",
            "rerank_top_n": 5,
        },
    )
    assert config_response.status_code == 201, config_response.text
    return corpus_response.json(), config_response.json()


async def ingest(
    client: AsyncClient,
    headers: dict[str, str],
    corpus_id: str,
    *,
    name: str,
    content: str,
    effective_date: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/corpora/{corpus_id}/ingest",
        headers=headers,
        json={
            "name": name,
            "source_type": "markdown",
            "content": content,
            "mime_type": "text/markdown",
            "effective_date": effective_date,
            "trust_level": "trusted",
            "metadata": {"locale": "en"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(payload)


@pytest.mark.asyncio
async def test_text_markdown_pdf_ingestion_and_hybrid_retrieval(client: AsyncClient) -> None:
    headers, project = await register(client, "formats")
    corpus, _ = await create_corpus_and_config(client, headers, project["id"], "Formats")
    text_response = await client.post(
        f"/v1/corpora/{corpus['id']}/ingest",
        headers=headers,
        json={
            "name": "plain.txt",
            "source_type": "text",
            "content": "Policy TEXT-14 requires plain text evidence retention for 14 days.",
            "mime_type": "text/plain",
            "trust_level": "standard",
            "metadata": {"locale": "en"},
        },
    )
    assert text_response.status_code == 201, text_response.text
    markdown = await ingest(
        client,
        headers,
        corpus["id"],
        name="refund.md",
        content="# Refunds\nRefund requests must be submitted within 14 days.",
    )
    pdf_response = await client.post(
        f"/v1/corpora/{corpus['id']}/upload",
        headers=headers,
        data={"source_type": "pdf", "trust_level": "trusted", "metadata": '{"locale":"en"}'},
        files={
            "file": (
                "shipping.pdf",
                minimal_pdf("PDF-SHIP-7 shipping evidence is retained for 7 days."),
                "application/pdf",
            )
        },
    )
    assert pdf_response.status_code == 201, pdf_response.text
    assert text_response.json()["chunks"]
    assert markdown["chunks"][0]["section_title"] == "Refunds"
    assert "PDF-SHIP-7" in pdf_response.json()["chunks"][0]["text"]

    retrieval = await client.post(
        f"/v1/corpora/{corpus['id']}/retrieve",
        headers=headers,
        json={"query": "refund requests within 14 days", "top_k": 3},
    )
    assert retrieval.status_code == 200, retrieval.text
    body = retrieval.json()
    assert body["dense_hits"]
    assert body["sparse_hits"]
    assert body["hits"]
    assert body["hits"][0]["document_id"] == markdown["document"]["id"]
    assert body["hits"][0]["rerank_score"] is not None
    assert all(hit["metadata"]["corpus_id"] == corpus["id"] for hit in body["hits"])


@pytest.mark.asyncio
async def test_cross_tenant_qdrant_filter_returns_zero_foreign_hits(client: AsyncClient) -> None:
    headers_a, project_a = await register(client, "tenant-a")
    headers_b, project_b = await register(client, "tenant-b")
    corpus_a, _ = await create_corpus_and_config(client, headers_a, project_a["id"], "Private A")
    corpus_b, _ = await create_corpus_and_config(client, headers_b, project_b["id"], "Private B")
    evidence_a = await ingest(
        client,
        headers_a,
        corpus_a["id"],
        name="a.md",
        content="# Private A\nTenant A secret policy marker ALPHA-ONLY.",
    )
    evidence_b = await ingest(
        client,
        headers_b,
        corpus_b["id"],
        name="b.md",
        content="# Private B\nTenant B secret policy marker BETA-ONLY.",
    )

    normal = await client.post(
        f"/v1/corpora/{corpus_a['id']}/retrieve",
        headers=headers_a,
        json={"query": "ALPHA-ONLY", "top_k": 5},
    )
    assert normal.status_code == 200
    assert {hit["document_id"] for hit in normal.json()["hits"]} == {evidence_a["document"]["id"]}
    unauthorized = await client.post(
        f"/v1/corpora/{corpus_b['id']}/retrieve",
        headers=headers_a,
        json={"query": "BETA-ONLY"},
    )
    assert unauthorized.status_code == 404

    settings = get_settings()
    store = QdrantVectorStore(
        settings.qdrant_url,
        settings.qdrant_collection_prefix,
        settings.embedding_vector_size,
        settings.rag_retrieval_timeout_seconds,
    )
    embedder = DeterministicEmbeddingProvider(settings.embedding_vector_size)
    try:
        forged_scope = RetrievalScope(
            organization_id=project_a["organization_id"],
            project_id=project_b["id"],
            corpus_id=corpus_b["id"],
        )
        dense = await store.query_dense(
            scope=forged_scope,
            vector=await embedder.embed_query("BETA-ONLY"),
            filters={},
            limit=5,
        )
        sparse = await store.query_sparse(
            scope=forged_scope,
            vector=sparse_vector("BETA-ONLY"),
            filters={},
            limit=5,
        )
    finally:
        await store.close()
    assert dense == []
    assert sparse == []
    assert evidence_b["document"]["id"] not in {hit.document_id for hit in [*dense, *sparse]}


@pytest.mark.asyncio
async def test_rag_langgraph_acceptance_failures_persist_with_correct_primary_reasons(
    client: AsyncClient,
) -> None:
    headers, project = await register(client, "rag-e2e")
    corpus, config = await create_corpus_and_config(client, headers, project["id"], "Policies v2")
    old = await ingest(
        client,
        headers,
        corpus["id"],
        name="refund_policy_v2.md",
        content="# Refund Policy\nRefund requests may be submitted within 30 days.",
        effective_date="2025-01-01T00:00:00Z",
    )
    current = await ingest(
        client,
        headers,
        corpus["id"],
        name="refund_policy_v2.md",
        content="# Refund Policy\nRefund requests must be submitted within 14 days.",
    )
    shipping = await ingest(
        client,
        headers,
        corpus["id"],
        name="shipping.md",
        content="# Shipping\nStandard shipping takes 3 to 5 business days.",
    )
    agent = (
        await client.post(
            f"/v1/projects/{project['id']}/agents",
            headers=headers,
            json={"name": "RAG Research Agent", "description": "Integration demo"},
        )
    ).json()
    version_response = await client.post(
        f"/v1/agents/{agent['id']}/versions",
        headers=headers,
        json={
            "version": "v1",
            "adapter_type": "demo_rag_agent",
            "model_provider": "demo",
            "model_name": "deterministic-rag-research-v1",
            "system_prompt": "Use retrieved evidence.",
            "config": {},
            "tool_registry": [],
        },
    )
    assert version_response.status_code == 201, version_response.text
    version = version_response.json()
    suite = (
        await client.post(
            f"/v1/projects/{project['id']}/suites",
            headers=headers,
            json={
                "name": "RAG acceptance",
                "description": "Root cause acceptance",
                "version": "1",
                "gate_policy": {"block_severities": ["critical"]},
            },
        )
    ).json()
    definitions = [
        (
            "Unsupported claim",
            "Can customers refund after 30 days?",
            {
                "rag_enabled": True,
                "demo_behavior": "unsupported_claim",
                "demo_answer": "Customers can refund within 30 days.",
                "retrieval_filters": {"version": "2"},
            },
            "UNSUPPORTED_CLAIM",
        ),
        (
            "Retrieval failure",
            "What is the current refund deadline?",
            {
                "rag_enabled": True,
                "demo_behavior": "grounded",
                "retrieval_filters": {"document_id": shipping["document"]["id"]},
            },
            "GOLD_EVIDENCE_NOT_RETRIEVED",
        ),
        (
            "Stale source",
            "What is the refund deadline?",
            {
                "rag_enabled": True,
                "demo_behavior": "grounded",
                "retrieval_filters": {"version": "1"},
            },
            "STALE_SOURCE_USED",
        ),
    ]
    scenario_ids: dict[str, str] = {}
    for name, question, metadata, _ in definitions:
        response = await client.post(
            f"/v1/suites/{suite['id']}/scenarios",
            headers=headers,
            json={
                "name": name,
                "input": question,
                "expected_tools": [],
                "forbidden_tools": [],
                "tags": ["rag", "acceptance"],
                "severity": "critical",
                "timeout_seconds": 10,
                "metadata": metadata,
            },
        )
        assert response.status_code == 201, response.text
        scenario = response.json()
        scenario_ids[scenario["id"]] = name
        gold_response = await client.post(
            f"/v1/scenarios/{scenario['id']}/gold-evidence",
            headers=headers,
            json={
                "document_id": current["document"]["id"],
                "document_version_id": current["version"]["id"],
                "chunk_id": current["chunks"][0]["id"],
                "relevance_score": 1.0,
                "required": True,
                "metadata": {"expected_text": "Refund requests must be submitted within 14 days."},
            },
        )
        assert gold_response.status_code == 201, gold_response.text

    created_run = await client.post(
        "/v1/runs",
        headers=headers,
        json={
            "project_id": project["id"],
            "test_suite_id": suite["id"],
            "agent_version_id": version["id"],
            "corpus_id": corpus["id"],
            "rag_config_id": config["id"],
        },
    )
    assert created_run.status_code == 202, created_run.text
    run = created_run.json()
    assert run["snapshot"]["rag"]["corpus_id"] == corpus["id"]
    assert run["snapshot"]["scenarios"][0]["gold_evidence"]
    await execute_run({}, run["id"])

    cases = (await client.get(f"/v1/runs/{run['id']}/cases", headers=headers)).json()
    by_name = {scenario_ids[item["scenario_id"]]: item for item in cases}
    for name, _, _, reason in definitions:
        assert by_name[name]["reason_codes"][0] == reason
        assert by_name[name]["verdict"] == "block"
    unsupported = by_name["Unsupported claim"]
    trace_response = await client.get(f"/v1/cases/{unsupported['id']}/trace", headers=headers)
    assert trace_response.status_code == 200
    trace = trace_response.json()
    event_types = {event["event_type"] for event in trace["traces"]}
    assert {
        "retrieval_query",
        "dense_retrieval_results",
        "sparse_retrieval_results",
        "hybrid_retrieval_results",
        "reranking_completed",
        "retrieval_evidence_selected",
        "citation_emitted",
        "claim_extracted",
        "citation_evaluated",
        "groundedness_evaluated",
    } <= event_types
    evaluations = {item["metric"]: item for item in trace["evaluations"]}
    assert evaluations["retrieval_recall_at_5"]["passed"] is True
    assert evaluations["citation_exists"]["passed"] is True
    assert evaluations["citation_support"]["passed"] is False
    assert evaluations["groundedness"]["passed"] is False
    assert "14 days" in str(evaluations["groundedness"]["expected"])
    assert "30 days" in str(evaluations["groundedness"]["actual"])
    assert (await client.get(f"/v1/cases/{unsupported['id']}/retrieval", headers=headers)).json()
    assert (await client.get(f"/v1/cases/{unsupported['id']}/claims", headers=headers)).json()
    assert (await client.get(f"/v1/cases/{unsupported['id']}/citations", headers=headers)).json()

    completed = (await client.get(f"/v1/runs/{run['id']}", headers=headers)).json()
    assert completed["status"] == "completed"
    assert completed["verdict"] == "block"
    for metric in (
        "retrieval_recall_at_1",
        "retrieval_recall_at_3",
        "retrieval_recall_at_5",
        "mrr",
        "ndcg",
        "citation_precision",
        "citation_recall",
        "groundedness",
        "unsupported_claim_rate",
        "rag_average_latency",
        "rag_p95_latency",
    ):
        assert metric in completed["metrics"]
    assert old["version"]["id"] != current["version"]["id"]


@pytest.mark.asyncio
async def test_rag_seed_is_idempotent(client: AsyncClient) -> None:
    del client
    await seed_rag()
    await seed_rag()
    async with SessionLocal() as session:
        project = await session.scalar(select(Corpus).where(Corpus.name == "Company Policies"))
        assert project is not None
        suite_scenarios = await session.scalar(
            select(func.count(Scenario.id)).where(
                Scenario.test_suite_id
                == select(Scenario.test_suite_id)
                .where(Scenario.name == "Refund policy factual pass")
                .scalar_subquery()
            )
        )
        document_count = await session.scalar(
            select(func.count(Document.id)).where(Document.corpus_id == project.id)
        )
        version_count = await session.scalar(
            select(func.count(DocumentVersion.id))
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.corpus_id == project.id)
        )
        gold_count = await session.scalar(select(func.count(GoldEvidence.id)))
    assert suite_scenarios == len(RAG_SCENARIOS)
    assert document_count == 7
    assert version_count == 8
    assert gold_count is not None and gold_count >= len(RAG_SCENARIOS)
