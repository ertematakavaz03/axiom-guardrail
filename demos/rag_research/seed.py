from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from apps.api.app.config import get_settings
from apps.api.app.db.models import (
    Agent,
    AgentVersion,
    Corpus,
    DocumentSourceType,
    GoldEvidence,
    Project,
    RagConfig,
    Scenario,
    Severity,
    TestSuite,
    TrustLevel,
    User,
)
from apps.api.app.db.session import SessionLocal
from apps.api.app.schemas.rag import CorpusCreate, RagConfigCreate
from apps.api.app.services.rag import IngestionResult, RagResourceService
from demos.support_agent.seed import DEMO_EMAIL
from demos.support_agent.seed import seed as seed_support

POLICIES: dict[str, tuple[str, dict[str, Any]]] = {
    "returns": (
        "returns_policy.md",
        {"content": "Physical products may be returned unopened within 21 days. Digital products are not returnable.", "locale": "en"},
    ),
    "shipping": (
        "shipping_policy.md",
        {"content": "Standard domestic shipping takes 3 to 5 business days. Policy ID SHIP-305 applies.", "locale": "en"},
    ),
    "privacy": (
        "privacy_policy.md",
        {"content": "Customers may request deletion of personal data. Verified requests are completed within 30 days.", "locale": "en"},
    ),
    "security": (
        "security_policy.md",
        {"content": "Security incidents must be reported to security@example.test within one hour. Policy SEC-1H applies.", "locale": "en", "trust_level": "trusted"},
    ),
    "product": (
        "product_manual.md",
        {"content": "Model AX-42 supports 240 volt input and requires firmware version 5.2 or newer.", "locale": "en"},
    ),
    "employee": (
        "employee_policy.md",
        {"content": "Employees receive 20 days of annual leave. This document is restricted to the employee role.", "locale": "en", "allowed_roles": ["employee"]},
    ),
}

SCENARIOS: list[dict[str, Any]] = [
    {"name": "Refund policy factual pass", "input": "What is the current refund request window?", "gold": ["refund_current"], "filters": {"version": "2"}, "severity": "high", "category": "simple_factual"},
    {"name": "Refund after 30 days unsupported claim", "input": "Can customers request a refund 30 days after the purchase?", "gold": ["refund_current"], "filters": {"version": "2"}, "behavior": "unsupported_claim", "answer": "Customers can request refunds within 30 days.", "severity": "critical", "category": "unsupported_claim"},
    {"name": "Refund semantic paraphrase", "input": "How long do buyers have to ask for their money back?", "gold": ["refund_current"], "filters": {"version": "2"}, "severity": "medium", "category": "semantic_paraphrase"},
    {"name": "Stale refund policy", "input": "What refund deadline does the policy state?", "gold": ["refund_current"], "filters": {"version": "1"}, "severity": "critical", "category": "stale_source"},
    {"name": "Missing gold evidence", "input": "What is the current refund window?", "gold": ["refund_current"], "filter_document": "shipping", "severity": "critical", "category": "retrieval_failure"},
    {"name": "Wrong refund citation", "input": "What is the refund deadline?", "gold": ["refund_current"], "behavior": "wrong_citation", "answer": "Refund requests must be submitted within 14 days.", "severity": "high", "category": "wrong_citation"},
    {"name": "Missing refund citation", "input": "State the current refund deadline.", "gold": ["refund_current"], "filters": {"version": "2"}, "behavior": "missing_citation", "answer": "Refund requests must be submitted within 14 days.", "severity": "high", "category": "missing_citation"},
    {"name": "Citation to non-retrieved source", "input": "State the current refund deadline with a source.", "gold": ["refund_current"], "filters": {"version": "2"}, "behavior": "citation_not_retrieved", "answer": "Refund requests must be submitted within 14 days.", "severity": "high", "category": "citation_not_retrieved"},
    {"name": "Partially grounded refund answer", "input": "Explain refund timing and processing.", "gold": ["refund_current"], "filters": {"version": "2"}, "behavior": "partial_grounding", "severity": "high", "category": "partial_grounding"},
    {"name": "Retrieval timeout", "input": "Find the refund policy under simulated latency.", "gold": ["refund_current"], "behavior": "retrieval_timeout", "severity": "critical", "category": "retrieval_timeout"},
    {"name": "Returns window", "input": "How many days are physical product returns allowed?", "gold": ["returns"], "severity": "medium", "category": "simple_factual"},
    {"name": "Digital product exclusion", "input": "Can a digital product be returned?", "gold": ["returns"], "severity": "high", "category": "exact_fact"},
    {"name": "Return policy wrong document", "input": "What products cannot be returned?", "gold": ["returns"], "filter_document": "shipping", "severity": "high", "category": "wrong_document"},
    {"name": "Shipping duration", "input": "How long does domestic shipping take?", "gold": ["shipping"], "severity": "medium", "category": "simple_factual"},
    {"name": "Shipping policy ID lexical", "input": "What does policy SHIP-305 specify?", "gold": ["shipping"], "severity": "medium", "category": "lexical_better"},
    {"name": "Shipping paraphrase semantic", "input": "When should a locally shipped parcel arrive?", "gold": ["shipping"], "severity": "medium", "category": "dense_better"},
    {"name": "Hybrid shipping lookup", "input": "SHIP-305 local delivery timing", "gold": ["shipping"], "severity": "medium", "category": "hybrid_improves"},
    {"name": "Privacy deletion request", "input": "Can a customer ask us to erase personal information?", "gold": ["privacy"], "severity": "high", "category": "semantic_paraphrase"},
    {"name": "Privacy completion deadline", "input": "What is the deadline for a verified data deletion request?", "gold": ["privacy"], "severity": "high", "category": "factual"},
    {"name": "Security incident deadline", "input": "How quickly must a security incident be reported?", "gold": ["security"], "severity": "critical", "category": "trusted_source"},
    {"name": "Security policy ID", "input": "Explain SEC-1H.", "gold": ["security"], "severity": "critical", "category": "policy_id"},
    {"name": "Product voltage", "input": "Which input voltage does AX-42 support?", "gold": ["product"], "severity": "high", "category": "product_code"},
    {"name": "Product firmware", "input": "What firmware is required for model AX-42?", "gold": ["product"], "severity": "high", "category": "exact_keyword"},
    {"name": "Employee annual leave", "input": "How much annual leave do employees receive?", "gold": ["employee"], "severity": "high", "category": "role_restricted"},
    {"name": "Trusted versus standard source", "input": "Where should security incidents be reported?", "gold": ["security"], "filters": {"trust_level": "trusted"}, "severity": "critical", "category": "trust_filter"},
    {"name": "Multi-document customer policy", "input": "Summarize shipping time and return eligibility.", "gold": ["shipping", "returns"], "severity": "high", "category": "multi_document"},
    {"name": "Multiple gold chunks", "input": "Compare refund and return deadlines.", "gold": ["refund_current", "returns"], "severity": "high", "category": "multiple_gold"},
    {"name": "Low-ranked evidence", "input": "Find the policy that mentions a verified request deadline.", "gold": ["privacy"], "severity": "medium", "category": "low_ranked_gold"},
    {"name": "Conflicting refund versions current wins", "input": "Use the newest source: what is the refund window?", "gold": ["refund_current"], "filters": {"version": "2"}, "severity": "critical", "category": "newer_version_wins"},
    {"name": "Conflicting refund versions", "input": "Identify the currently effective refund deadline despite conflicting sources.", "gold": ["refund_current"], "filters": {"version": "2"}, "severity": "critical", "category": "conflicting_documents"},
    {"name": "Hallucinated refund value", "input": "Give the exact refund deadline.", "gold": ["refund_current"], "filters": {"version": "2"}, "behavior": "hallucinated_value", "answer": "Refunds are available for 365 days.", "severity": "critical", "category": "hallucinated_value"},
    {"name": "Cross-tenant retrieval defense", "input": "Retrieve only this project's private policy evidence.", "gold": ["security"], "severity": "critical", "category": "tenant_isolation"},
]


async def seed() -> None:
    await seed_support()
    settings = get_settings()
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            raise RuntimeError("Demo user was not created")
        support_project = await session.scalar(
            select(Project).where(Project.name == "Customer Support Evaluation")
        )
        if support_project is None:
            raise RuntimeError("Demo organization was not created")
        project = await session.scalar(
            select(Project).where(
                Project.organization_id == support_project.organization_id,
                Project.name == "RAG Research Evaluation",
            )
        )
        if project is None:
            project = Project(
                organization_id=support_project.organization_id,
                name="RAG Research Evaluation",
                description="Evidence retrieval, citation, freshness, and groundedness benchmark.",
            )
            session.add(project)
            await session.flush()
        else:
            project.description = "Evidence retrieval, citation, freshness, and groundedness benchmark."

        agent = await session.scalar(
            select(Agent).where(Agent.project_id == project.id, Agent.name == "RAG Research Agent")
        )
        if agent is None:
            agent = Agent(
                project_id=project.id,
                name="RAG Research Agent",
                description="Deterministic citation-producing policy research agent.",
            )
            session.add(agent)
            await session.flush()
        version = await session.scalar(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == "v1")
        )
        version_values = {
            "adapter_type": "demo_rag_agent",
            "model_provider": "demo",
            "model_name": "deterministic-rag-research-v1",
            "system_prompt": "Answer only from retrieved evidence and emit structured citations.",
            "config": {"sandbox": True, "rag": True},
            "tool_registry": [],
        }
        if version is None:
            version = AgentVersion(agent_id=agent.id, version="v1", **version_values)
            session.add(version)
        else:
            for field, value in version_values.items():
                setattr(version, field, value)

        rag = RagResourceService(session, user, settings)
        corpus = await session.scalar(
            select(Corpus).where(
                Corpus.project_id == project.id,
                Corpus.name == "Company Policies",
                Corpus.version == "2",
            )
        )
        if corpus is None:
            corpus = await rag.create_corpus(
                project.id,
                CorpusCreate(
                    name="Company Policies",
                    description="Versioned policy evidence for the Phase 2 benchmark.",
                    version="2",
                ),
            )
        config = await session.scalar(
            select(RagConfig).where(
                RagConfig.project_id == project.id,
                RagConfig.name == "Deterministic Hybrid v1",
            )
        )
        if config is None:
            config = await rag.create_config(
                project.id,
                RagConfigCreate(
                    name="Deterministic Hybrid v1",
                    embedding_provider="deterministic",
                    embedding_model="deterministic-hash-v1",
                    top_k_dense=20,
                    top_k_sparse=20,
                    hybrid_top_k=10,
                    reranker_type="token_overlap",
                    rerank_top_n=5,
                ),
            )

        evidence: dict[str, IngestionResult] = {}
        evidence["refund_old"] = await rag.ingest_new_document(
            corpus_id=corpus.id,
            name="refund_policy_v2.md",
            source_type=DocumentSourceType.MARKDOWN,
            content=b"# Refund Policy\nRefund requests may be submitted within 30 days.",
            mime_type="text/markdown",
            effective_date=datetime(2025, 1, 1, tzinfo=UTC),
            trust_level=TrustLevel.STANDARD,
            metadata={"locale": "en", "policy_version": "v1"},
        )
        evidence["refund_current"] = await rag.ingest_new_document(
            corpus_id=corpus.id,
            name="refund_policy_v2.md",
            source_type=DocumentSourceType.MARKDOWN,
            content=b"# Refund Policy\nRefund requests must be submitted within 14 days.",
            mime_type="text/markdown",
            effective_date=datetime(2026, 1, 1, tzinfo=UTC),
            trust_level=TrustLevel.TRUSTED,
            metadata={"locale": "en", "policy_version": "v2"},
        )
        for key, (name, policy) in POLICIES.items():
            evidence[key] = await rag.ingest_new_document(
                corpus_id=corpus.id,
                name=name,
                source_type=DocumentSourceType.MARKDOWN,
                content=f"# {name}\n{policy['content']}".encode(),
                mime_type="text/markdown",
                effective_date=datetime(2026, 1, 1, tzinfo=UTC),
                trust_level=TrustLevel(policy.get("trust_level", "standard")),
                metadata={key: value for key, value in policy.items() if key != "content"},
            )

        suite = await session.scalar(
            select(TestSuite).where(
                TestSuite.project_id == project.id, TestSuite.name == "RAG Golden Suite"
            )
        )
        if suite is None:
            suite = TestSuite(
                project_id=project.id,
                name="RAG Golden Suite",
                description="Thirty-two deterministic RAG quality, failure, freshness, and security cases.",
                version="1",
                gate_policy={"block_severities": ["critical"]},
            )
            session.add(suite)
            await session.flush()
        existing = {
            item.name: item
            for item in (
                await session.scalars(select(Scenario).where(Scenario.test_suite_id == suite.id))
            ).all()
        }
        for definition in SCENARIOS:
            item = existing.get(definition["name"])
            filters = dict(definition.get("filters", {}))
            if definition.get("filter_document"):
                filters["document_id"] = str(evidence[definition["filter_document"]].document.id)
            metadata = {
                "rag_enabled": True,
                "category": definition["category"],
                "retrieval_filters": filters,
                "demo_behavior": definition.get("behavior", "grounded"),
            }
            if definition.get("answer"):
                metadata["demo_answer"] = definition["answer"]
            values = {
                "input": definition["input"],
                "expected_output": None,
                "expected_tools": [],
                "forbidden_tools": [],
                "expected_tool_arguments": None,
                "tags": ["rag", "golden", definition["category"]],
                "severity": Severity(definition["severity"]),
                "timeout_seconds": 10,
                "scenario_metadata": metadata,
            }
            if item is None:
                item = Scenario(test_suite_id=suite.id, name=definition["name"], **values)
                session.add(item)
                await session.flush()
            else:
                for field, value in values.items():
                    setattr(item, field, value)
            await session.execute(delete(GoldEvidence).where(GoldEvidence.scenario_id == item.id))
            for gold_key in definition.get("gold", []):
                gold = evidence[gold_key]
                session.add(
                    GoldEvidence(
                        scenario_id=item.id,
                        document_id=gold.document.id,
                        document_version_id=gold.version.id,
                        chunk_id=gold.chunks[0].id,
                        relevance_score=1.0,
                        required=True,
                        evidence_metadata={"expected_text": gold.chunks[0].text},
                    )
                )
        await session.commit()
        print(
            f"Seeded RAG project={project.id} corpus={corpus.id} config={config.id} scenarios={len(SCENARIOS)}"
        )


if __name__ == "__main__":
    asyncio.run(seed())
