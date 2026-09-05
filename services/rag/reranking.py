from __future__ import annotations

from typing import Protocol

from services.rag.errors import RerankerError
from services.rag.models import RetrievalHit
from services.rag.sparse import lexical_overlap


class Reranker(Protocol):
    name: str

    async def rerank(
        self, query: str, candidates: list[RetrievalHit], top_n: int
    ) -> list[RetrievalHit]: ...


class NoopReranker:
    name = "none"

    async def rerank(
        self, query: str, candidates: list[RetrievalHit], top_n: int
    ) -> list[RetrievalHit]:
        del query
        return [
            hit.model_copy(update={"rank": index + 1})
            for index, hit in enumerate(candidates[:top_n])
        ]


class TokenOverlapReranker:
    name = "token_overlap"

    async def rerank(
        self, query: str, candidates: list[RetrievalHit], top_n: int
    ) -> list[RetrievalHit]:
        scored = [
            hit.model_copy(update={"rerank_score": lexical_overlap(query, hit.content)})
            for hit in candidates
        ]
        scored.sort(
            key=lambda hit: (hit.rerank_score or 0.0, hit.hybrid_score or 0.0), reverse=True
        )
        return [
            hit.model_copy(update={"rank": index + 1}) for index, hit in enumerate(scored[:top_n])
        ]


def reranker_for(name: str) -> Reranker:
    if name in {"none", "noop"}:
        return NoopReranker()
    if name in {"token_overlap", "deterministic"}:
        return TokenOverlapReranker()
    raise RerankerError("Unsupported reranker", details={"reranker_type": name})
