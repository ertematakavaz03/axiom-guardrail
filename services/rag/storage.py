from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from qdrant_client import AsyncQdrantClient, models

from services.rag.errors import QdrantTimeoutError, QdrantUnavailableError
from services.rag.models import RetrievalHit, RetrievalScope, SparseRepresentation

ALLOWED_FILTER_FIELDS = {
    "document_type": "source_type",
    "locale": "locale",
    "version": "version",
    "effective_date": "effective_date",
    "trust_level": "trust_level",
    "document_id": "document_id",
}


def collection_name_for(prefix: str, project_id: str) -> str:
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", prefix)[:32]
    project_key = re.sub(r"[^a-fA-F0-9]", "", project_id)
    return f"{safe_prefix}_{project_key}"


class QdrantVectorStore:
    def __init__(
        self,
        url: str,
        collection_prefix: str,
        vector_size: int,
        timeout_seconds: int = 10,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self.client = client or AsyncQdrantClient(url=url, timeout=timeout_seconds)
        self.collection_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", collection_prefix)[:32]
        self.vector_size = vector_size
        self.timeout_seconds = timeout_seconds

    def collection_name(self, project_id: str) -> str:
        return collection_name_for(self.collection_prefix, project_id)

    async def close(self) -> None:
        await self.client.close()

    async def health(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.client.get_collections()
            return True
        except TimeoutError as exc:
            raise QdrantTimeoutError("Qdrant health check timed out") from exc
        except Exception as exc:
            raise QdrantUnavailableError("Qdrant is unavailable") from exc

    async def ensure_collection(self, project_id: str) -> str:
        collection = self.collection_name(project_id)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                exists = await self.client.collection_exists(collection)
                if not exists:
                    await self.client.create_collection(
                        collection_name=collection,
                        vectors_config={
                            "dense": models.VectorParams(
                                size=self.vector_size, distance=models.Distance.COSINE
                            )
                        },
                        sparse_vectors_config={
                            "sparse": models.SparseVectorParams(
                                index=models.SparseIndexParams(on_disk=False)
                            )
                        },
                    )
                    for field in (
                        "organization_id",
                        "project_id",
                        "corpus_id",
                        "document_id",
                        "document_version_id",
                        "chunk_id",
                        "trust_level",
                        "locale",
                        "version",
                    ):
                        await self.client.create_payload_index(
                            collection_name=collection,
                            field_name=field,
                            field_schema=models.PayloadSchemaType.KEYWORD,
                            wait=True,
                        )
            return collection
        except TimeoutError as exc:
            raise QdrantTimeoutError("Qdrant collection setup timed out") from exc
        except Exception as exc:
            raise QdrantUnavailableError("Qdrant collection setup failed") from exc

    async def upsert(
        self,
        *,
        project_id: str,
        point_ids: list[str],
        dense_vectors: list[list[float]],
        sparse_vectors: list[SparseRepresentation],
        payloads: list[dict[str, Any]],
    ) -> str:
        if not (len(point_ids) == len(dense_vectors) == len(sparse_vectors) == len(payloads)):
            raise ValueError("Qdrant upsert arrays must have equal lengths")
        collection = await self.ensure_collection(project_id)
        points = [
            models.PointStruct(
                id=point_id,
                vector={
                    "dense": dense,
                    "sparse": models.SparseVector(
                        indices=sparse.indices,
                        values=sparse.values,
                    ),
                },
                payload=payload,
            )
            for point_id, dense, sparse, payload in zip(
                point_ids, dense_vectors, sparse_vectors, payloads, strict=True
            )
        ]
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.client.upsert(collection_name=collection, points=points, wait=True)
            return collection
        except TimeoutError as exc:
            raise QdrantTimeoutError("Qdrant upsert timed out") from exc
        except Exception as exc:
            raise QdrantUnavailableError("Qdrant upsert failed") from exc

    async def query_dense(
        self,
        *,
        scope: RetrievalScope,
        vector: list[float],
        filters: dict[str, Any],
        limit: int,
    ) -> list[RetrievalHit]:
        return await self._query(
            scope=scope,
            query=vector,
            using="dense",
            filters=filters,
            limit=limit,
        )

    async def query_sparse(
        self,
        *,
        scope: RetrievalScope,
        vector: SparseRepresentation,
        filters: dict[str, Any],
        limit: int,
    ) -> list[RetrievalHit]:
        return await self._query(
            scope=scope,
            query=models.SparseVector(indices=vector.indices, values=vector.values),
            using="sparse",
            filters=filters,
            limit=limit,
        )

    async def _query(
        self,
        *,
        scope: RetrievalScope,
        query: list[float] | models.SparseVector,
        using: Literal["dense", "sparse"],
        filters: dict[str, Any],
        limit: int,
    ) -> list[RetrievalHit]:
        collection = self.collection_name(scope.project_id)
        query_filter = self.build_filter(scope, filters)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.query_points(
                    collection_name=collection,
                    query=query,
                    using=using,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                )
        except TimeoutError as exc:
            raise QdrantTimeoutError(f"Qdrant {using} retrieval timed out") from exc
        except Exception as exc:
            raise QdrantUnavailableError(f"Qdrant {using} retrieval failed") from exc
        hits: list[RetrievalHit] = []
        for rank, point in enumerate(response.points, start=1):
            payload = dict(point.payload or {})
            score = float(point.score)
            hits.append(
                RetrievalHit(
                    chunk_id=str(payload.get("chunk_id", "")),
                    document_id=str(payload.get("document_id", "")),
                    document_version_id=str(payload.get("document_version_id", "")),
                    document_name=str(payload.get("document_name", "")),
                    content=str(payload.get("content", "")),
                    dense_score=score if using == "dense" else None,
                    sparse_score=score if using == "sparse" else None,
                    dense_rank=rank if using == "dense" else None,
                    sparse_rank=rank if using == "sparse" else None,
                    rank=rank,
                    metadata={key: value for key, value in payload.items() if key != "content"},
                )
            )
        return hits

    @staticmethod
    def build_filter(scope: RetrievalScope, filters: dict[str, Any]) -> models.Filter:
        unknown = set(filters) - set(ALLOWED_FILTER_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported metadata filters: {', '.join(sorted(unknown))}")
        required = {
            "organization_id": scope.organization_id,
            "project_id": scope.project_id,
            "corpus_id": scope.corpus_id,
        }
        conditions = [
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in required.items()
        ]
        for requested, value in filters.items():
            if value is None:
                continue
            conditions.append(
                models.FieldCondition(
                    key=ALLOWED_FILTER_FIELDS[requested],
                    match=models.MatchValue(
                        value=str(value) if requested == "version" else value
                    ),
                )
            )
        return models.Filter(must=conditions)
