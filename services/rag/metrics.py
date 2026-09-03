from __future__ import annotations

import math

from services.rag.models import EvidenceMetricSet, GoldEvidenceRef, RetrievalHit


def evidence_matches(hit: RetrievalHit, gold: GoldEvidenceRef) -> bool:
    if gold.chunk_id:
        return hit.chunk_id == gold.chunk_id
    if gold.document_version_id:
        return hit.document_version_id == gold.document_version_id
    if gold.document_id:
        return hit.document_id == gold.document_id
    return False


def recall_at_k(hits: list[RetrievalHit], gold: list[GoldEvidenceRef], k: int) -> float | None:
    required = [item for item in gold if item.required]
    if not required:
        return None
    matched = sum(any(evidence_matches(hit, item) for hit in hits[:k]) for item in required)
    return matched / len(required)


def reciprocal_rank(hits: list[RetrievalHit], gold: list[GoldEvidenceRef]) -> float | None:
    if not gold:
        return None
    for rank, hit in enumerate(hits, start=1):
        if any(evidence_matches(hit, item) for item in gold):
            return 1.0 / rank
    return 0.0


def normalized_dcg(hits: list[RetrievalHit], gold: list[GoldEvidenceRef]) -> float | None:
    if not gold or not any(item.relevance_score != 1.0 for item in gold):
        return None
    gains = []
    for hit in hits:
        gains.append(
            max(
                (item.relevance_score for item in gold if evidence_matches(hit, item)),
                default=0.0,
            )
        )
    dcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sorted((item.relevance_score for item in gold), reverse=True)[: len(hits)]
    idcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def gold_hit_rate(hits: list[RetrievalHit], gold: list[GoldEvidenceRef]) -> float | None:
    if not gold:
        return None
    return sum(any(evidence_matches(hit, item) for hit in hits) for item in gold) / len(gold)


def retrieval_metrics(hits: list[RetrievalHit], gold: list[GoldEvidenceRef]) -> EvidenceMetricSet:
    return EvidenceMetricSet(
        recall_at_1=recall_at_k(hits, gold, 1),
        recall_at_3=recall_at_k(hits, gold, 3),
        recall_at_5=recall_at_k(hits, gold, 5),
        mrr=reciprocal_rank(hits, gold),
        ndcg=normalized_dcg(hits, gold),
        gold_evidence_hit_rate=gold_hit_rate(hits, gold),
    )
