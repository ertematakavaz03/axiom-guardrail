from __future__ import annotations

import asyncio
import time
from typing import Any

from services.rag.embeddings import EmbeddingProvider
from services.rag.errors import RetrievalError
from services.rag.models import (
    RetrievalHit,
    RetrievalResult,
    RetrievalScope,
    RetrievalTimings,
)
from services.rag.reranking import Reranker
from services.rag.sparse import sparse_vector
from services.rag.storage import QdrantVectorStore


def reciprocal_rank_fusion(
    dense_hits: list[RetrievalHit], sparse_hits: list[RetrievalHit], k: int = 60
) -> list[RetrievalHit]:
    merged: dict[str, RetrievalHit] = {}
    scores: dict[str, float] = {}
    for source, field in ((dense_hits, "dense"), (sparse_hits, "sparse")):
        for rank, hit in enumerate(source, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            current = merged.get(hit.chunk_id)
            if current is None:
                merged[hit.chunk_id] = hit.model_copy(deep=True)
            elif field == "dense":
                current.dense_score = hit.dense_score
                current.dense_rank = rank
            else:
                current.sparse_score = hit.sparse_score
                current.sparse_rank = rank
    ranked = [hit.model_copy(update={"hybrid_score": scores[chunk_id]}) for chunk_id, hit in merged.items()]
    ranked.sort(key=lambda hit: hit.hybrid_score or 0.0, reverse=True)
    return [hit.model_copy(update={"rank": index + 1}) for index, hit in enumerate(ranked)]


class RetrievalService:
    def __init__(
        self,
        store: QdrantVectorStore,
        embedder: EmbeddingProvider,
        reranker: Reranker,
        timeout_seconds: int = 10,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.timeout_seconds = timeout_seconds

    async def retrieve(
        self,
        *,
        query: str,
        scope: RetrievalScope,
        filters: dict[str, Any] | None = None,
        dense_enabled: bool = True,
        sparse_enabled: bool = True,
        top_k_dense: int = 20,
        top_k_sparse: int = 20,
        hybrid_top_k: int = 10,
        rerank_top_n: int = 5,
    ) -> RetrievalResult:
        if not query.strip():
            raise RetrievalError("Retrieval query must not be empty")
        if not dense_enabled and not sparse_enabled:
            raise RetrievalError("At least one retrieval mode must be enabled")
        started = time.perf_counter()
        embedding_ms = 0
        dense_hits: list[RetrievalHit] = []
        sparse_hits: list[RetrievalHit] = []
        query_filters = filters or {}

        async def dense() -> list[RetrievalHit]:
            nonlocal embedding_ms
            embed_started = time.perf_counter()
            vector = await self.embedder.embed_query(query)
            embedding_ms = round((time.perf_counter() - embed_started) * 1000)
            return await self.store.query_dense(
                scope=scope, vector=vector, filters=query_filters, limit=top_k_dense
            )

        async def sparse() -> list[RetrievalHit]:
            return await self.store.query_sparse(
                scope=scope,
                vector=sparse_vector(query),
                filters=query_filters,
                limit=top_k_sparse,
            )

        dense_started = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                tasks: list[asyncio.Task[list[RetrievalHit]]] = []
                if dense_enabled:
                    tasks.append(asyncio.create_task(dense()))
                if sparse_enabled:
                    tasks.append(asyncio.create_task(sparse()))
                results = await asyncio.gather(*tasks)
        except TimeoutError:
            from services.rag.errors import QdrantTimeoutError

            raise QdrantTimeoutError("Hybrid retrieval timed out") from None
        cursor = 0
        if dense_enabled:
            dense_hits = results[cursor]
            cursor += 1
        dense_ms = round((time.perf_counter() - dense_started) * 1000) if dense_enabled else 0
        sparse_hits = results[cursor] if sparse_enabled else []
        sparse_ms = round((time.perf_counter() - dense_started) * 1000) if sparse_enabled else 0

        fusion_started = time.perf_counter()
        fused = reciprocal_rank_fusion(dense_hits, sparse_hits)[:hybrid_top_k]
        fusion_ms = round((time.perf_counter() - fusion_started) * 1000)
        rerank_started = time.perf_counter()
        hits = await self.reranker.rerank(query, fused, rerank_top_n)
        reranking_ms = round((time.perf_counter() - rerank_started) * 1000)
        total_ms = round((time.perf_counter() - started) * 1000)
        return RetrievalResult(
            query=query,
            hits=hits,
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            candidate_count=len(fused),
            timings=RetrievalTimings(
                embedding_ms=embedding_ms,
                dense_ms=dense_ms,
                sparse_ms=sparse_ms,
                fusion_ms=fusion_ms,
                reranking_ms=reranking_ms,
                total_ms=total_ms,
            ),
        )
