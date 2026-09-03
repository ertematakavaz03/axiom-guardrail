from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from packages.agent_sdk.contracts import AgentCitation, AgentClaim
from services.evaluators.models import EvaluationContext, EvaluationResult
from services.rag.metrics import evidence_matches, retrieval_metrics
from services.rag.models import RetrievalHit
from services.rag.sparse import lexical_overlap

RAG_SYSTEM_FAILURES = {
    "QDRANT_TIMEOUT",
    "QDRANT_UNAVAILABLE",
    "EMBEDDING_FAILURE",
    "RETRIEVAL_FAILURE",
    "RERANKER_FAILURE",
}


def _result(
    metric: str,
    passed: bool,
    explanation: str,
    *,
    value: float | None = None,
    reason_code: str | None = None,
    expected: Any = None,
    actual: Any = None,
    evidence: dict[str, Any] | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        metric=metric,
        value=value,
        passed=passed,
        reason_code=reason_code,
        explanation=explanation,
        expected=expected,
        actual=actual,
        evidence=evidence or {},
    )


class RetrievalEvaluator:
    name = "retrieval"
    version = "2.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        failure = next(
            (
                error
                for error in context.errors
                if str(error.get("reason_code")) in RAG_SYSTEM_FAILURES
            ),
            None,
        )
        if failure:
            return [
                _result(
                    "retrieval_system",
                    False,
                    str(failure.get("explanation", "Retrieval failed")),
                    reason_code=str(failure["reason_code"]),
                    expected={"retrieval": "completed"},
                    actual={"retrieval": "failed"},
                )
            ]
        if context.retrieval is None:
            return [
                _result(
                    "retrieval_system",
                    False,
                    "RAG scenario produced no retrieval result",
                    reason_code="RETRIEVAL_FAILURE",
                )
            ]
        hits = context.retrieval.hits
        if context.rag_scope:
            leaked = [
                hit
                for hit in hits
                if any(
                    str(hit.metadata.get(key)) != getattr(context.rag_scope, key)
                    for key in ("organization_id", "project_id", "corpus_id")
                )
            ]
            if leaked:
                return [
                    _result(
                        "rag_tenant_scope",
                        False,
                        "Retrieved evidence crossed the immutable tenant/project/corpus scope",
                        reason_code="RAG_TENANT_SCOPE_VIOLATION",
                        expected=context.rag_scope.model_dump(),
                        actual=[hit.metadata for hit in leaked],
                    )
                ]
        metrics = retrieval_metrics(hits, context.gold_evidence)
        if not context.gold_evidence:
            results = [
                _result(
                    metric,
                    True,
                    "No gold evidence is defined; metric is not applicable",
                    value=None,
                )
                for metric in (
                    "retrieval_recall_at_1",
                    "retrieval_recall_at_3",
                    "retrieval_recall_at_5",
                    "mrr",
                    "ndcg",
                    "gold_evidence_hit_rate",
                )
            ]
        else:
            stale = self._stale_hit(hits, context)
            recall5 = metrics.recall_at_5 or 0.0
            required = [item for item in context.gold_evidence if item.required]
            reason = None
            explanation = "Required gold evidence was retrieved"
            if stale:
                reason = "STALE_SOURCE_USED"
                explanation = "A stale document version was retrieved instead of expected evidence"
            elif recall5 < 1.0:
                reason = "GOLD_EVIDENCE_NOT_RETRIEVED"
                explanation = "Required gold evidence was not present in the top five results"
            results = [
                _result("retrieval_recall_at_1", True, "Recall@1 measured", value=metrics.recall_at_1),
                _result("retrieval_recall_at_3", True, "Recall@3 measured", value=metrics.recall_at_3),
                _result(
                    "retrieval_recall_at_5",
                    reason is None,
                    explanation,
                    value=metrics.recall_at_5,
                    reason_code=reason,
                    expected=[item.model_dump() for item in required],
                    actual=[hit.model_dump() for hit in hits[:5]],
                    evidence=stale or {},
                ),
                _result("mrr", True, "Mean reciprocal rank contribution measured", value=metrics.mrr),
                _result("ndcg", True, "nDCG measured when graded relevance applies", value=metrics.ndcg),
                _result(
                    "gold_evidence_hit_rate",
                    True,
                    "Gold evidence hit rate measured",
                    value=metrics.gold_evidence_hit_rate,
                ),
            ]
        results.extend(
            [
                _result(
                    "rag_latency",
                    True,
                    "End-to-end retrieval latency measured",
                    value=float(context.retrieval.timings.total_ms),
                ),
                _result(
                    "embedding_latency",
                    True,
                    "Embedding latency measured",
                    value=float(context.retrieval.timings.embedding_ms),
                ),
                _result(
                    "reranking_latency",
                    True,
                    "Reranking latency measured",
                    value=float(context.retrieval.timings.reranking_ms),
                ),
            ]
        )
        return results

    @staticmethod
    def _stale_hit(
        hits: list[RetrievalHit], context: EvaluationContext
    ) -> dict[str, Any] | None:
        for gold in context.gold_evidence:
            if not gold.document_id or not gold.document_version_id:
                continue
            if any(evidence_matches(hit, gold) for hit in hits[:5]):
                continue
            stale = next(
                (
                    hit
                    for hit in hits[:5]
                    if hit.document_id == gold.document_id
                    and hit.document_version_id != gold.document_version_id
                ),
                None,
            )
            if stale:
                return {
                    "document_id": gold.document_id,
                    "expected_document_version_id": gold.document_version_id,
                    "actual_document_version_id": stale.document_version_id,
                    "actual_content": stale.content,
                }
        return None


class DeterministicClaimExtractor:
    name = "deterministic_sentence_claims"
    version = "2.0"

    def extract(self, response: str) -> list[AgentClaim]:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", response) if part.strip()]
        return [
            AgentClaim(id=f"claim_{index}", text=sentence, type="factual")
            for index, sentence in enumerate(sentences, start=1)
        ]


def claim_is_supported(claim: str, evidence: str) -> bool:
    claim_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", claim))
    evidence_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", evidence))
    if claim_numbers and not claim_numbers.issubset(evidence_numbers):
        return False
    return lexical_overlap(claim, evidence) >= 0.35


class CitationAndGroundednessEvaluator:
    name = "rag_evidence_quality"
    version = "2.0"

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]:
        if context.execution is None or context.retrieval is None:
            return []
        execution = context.execution
        claims = execution.claims or DeterministicClaimExtractor().extract(execution.final_response)
        citations = execution.citations
        hit_by_chunk = {hit.chunk_id: hit for hit in context.retrieval.hits}
        claim_by_id = {claim.id: claim for claim in claims if claim.type == "factual"}
        citation_findings: dict[str, str] = {}
        supportive_citations: set[str] = set()
        supported_claims: set[str] = set()
        citations_by_claim: dict[str, list[AgentCitation]] = defaultdict(list)

        for citation in citations:
            for claim_id in citation.claim_ids:
                citations_by_claim[claim_id].append(citation)
            hit = hit_by_chunk.get(citation.chunk_id)
            if hit is None:
                citation_findings[citation.citation_id] = "CITATION_NOT_RETRIEVED"
                continue
            if hit.document_id != citation.document_id:
                citation_findings[citation.citation_id] = "CITATION_NOT_FOUND"
                continue
            if context.rag_scope and any(
                str(hit.metadata.get(key)) != getattr(context.rag_scope, key)
                for key in ("organization_id", "project_id", "corpus_id")
            ):
                citation_findings[citation.citation_id] = "CITATION_OUT_OF_SCOPE"
                continue
            linked_claims = [claim_by_id[item] for item in citation.claim_ids if item in claim_by_id]
            if linked_claims and all(claim_is_supported(claim.text, hit.content) for claim in linked_claims):
                supportive_citations.add(citation.citation_id)
                supported_claims.update(claim.id for claim in linked_claims)
            else:
                citation_findings[citation.citation_id] = "WRONG_CITATION"

        existence_codes = [
            code
            for code in citation_findings.values()
            if code in {"CITATION_NOT_FOUND", "CITATION_OUT_OF_SCOPE", "CITATION_NOT_RETRIEVED"}
        ]
        existence_reason = existence_codes[0] if existence_codes else None
        results = [
            _result(
                "citation_exists",
                not existence_codes,
                "All structured citations resolve to retrieved in-scope evidence"
                if not existence_codes
                else "One or more citations do not resolve to retrieved in-scope evidence",
                value=1.0 if not existence_codes else 0.0,
                reason_code=existence_reason,
                expected={"citations": "retrieved and in scope"},
                actual=citation_findings,
            )
        ]
        wrong = [code for code in citation_findings.values() if code == "WRONG_CITATION"]
        results.append(
            _result(
                "citation_support",
                not wrong,
                "Cited evidence supports linked claims"
                if not wrong
                else "At least one citation contradicts or does not support its linked claim",
                value=(len(supportive_citations) / len(citations)) if citations else None,
                reason_code="WRONG_CITATION" if wrong else None,
                expected=[hit.content for hit in context.retrieval.hits],
                actual=[claim.model_dump() for claim in claims],
                evidence={"citation_findings": citation_findings},
            )
        )
        factual = list(claim_by_id)
        missing_claims = [claim_id for claim_id in factual if not citations_by_claim.get(claim_id)]
        unsupported_claims = [
            claim_id
            for claim_id in factual
            if citations_by_claim.get(claim_id) and claim_id not in supported_claims
        ]
        groundedness = len(supported_claims) / len(factual) if factual else None
        grounded = not missing_claims and not unsupported_claims
        ground_reason = "UNSUPPORTED_CLAIM" if unsupported_claims else (
            "MISSING_CITATION" if missing_claims else None
        )
        results.append(
            _result(
                "groundedness",
                grounded,
                "All factual claims are supported by cited evidence"
                if grounded
                else "One or more factual claims lack supporting evidence",
                value=groundedness,
                reason_code=ground_reason,
                expected={
                    "supported_factual_claims": len(factual),
                    "evidence": [hit.content for hit in context.retrieval.hits],
                },
                actual={
                    "claims": [claim.model_dump() for claim in claims],
                    "unsupported_claim_ids": unsupported_claims,
                    "missing_citation_claim_ids": missing_claims,
                },
                evidence={
                    "supported_claim_ids": sorted(supported_claims),
                    "citation_findings": citation_findings,
                },
            )
        )
        results.extend(
            [
                _result(
                    "citation_precision",
                    True,
                    "Supportive citations divided by emitted citations",
                    value=(len(supportive_citations) / len(citations)) if citations else None,
                ),
                _result(
                    "citation_recall",
                    True,
                    "Supported factual claims divided by factual claims requiring evidence",
                    value=groundedness,
                ),
                _result(
                    "unsupported_claim_rate",
                    True,
                    "Unsupported factual claims divided by factual claims",
                    value=(len(unsupported_claims) / len(factual)) if factual else None,
                ),
            ]
        )
        return results
