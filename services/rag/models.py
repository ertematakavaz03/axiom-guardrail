from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ParsedSection(BaseModel):
    text: str
    page_number: int | None = None
    section_title: str | None = None


class ChunkDraft(BaseModel):
    text: str
    token_count: int
    chunk_index: int
    page_number: int | None = None
    section_title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SparseRepresentation(BaseModel):
    indices: list[int]
    values: list[float]


class RetrievalScope(BaseModel):
    organization_id: str
    project_id: str
    corpus_id: str


class RetrievalHit(BaseModel):
    chunk_id: str
    document_id: str
    document_version_id: str
    content: str
    document_name: str = ""
    dense_score: float | None = None
    sparse_score: float | None = None
    hybrid_score: float | None = None
    rerank_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rank: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalTimings(BaseModel):
    embedding_ms: int = 0
    dense_ms: int = 0
    sparse_ms: int = 0
    fusion_ms: int = 0
    reranking_ms: int = 0
    total_ms: int = 0


class RetrievalResult(BaseModel):
    query: str
    hits: list[RetrievalHit]
    dense_hits: list[RetrievalHit] = Field(default_factory=list)
    sparse_hits: list[RetrievalHit] = Field(default_factory=list)
    candidate_count: int = 0
    timings: RetrievalTimings = Field(default_factory=RetrievalTimings)


class GoldEvidenceRef(BaseModel):
    document_id: str | None = None
    document_version_id: str | None = None
    chunk_id: str | None = None
    relevance_score: float = 1.0
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceMetricSet(BaseModel):
    recall_at_1: float | None
    recall_at_3: float | None
    recall_at_5: float | None
    mrr: float | None
    ndcg: float | None
    gold_evidence_hit_rate: float | None


class RetrievalConfigSnapshot(BaseModel):
    corpus_id: str
    corpus_version: str
    rag_config_id: str
    organization_id: str
    project_id: str
    embedding_provider: str
    embedding_model: str
    dense_enabled: bool = True
    sparse_enabled: bool = True
    retrieval_parameters: dict[str, Any] = Field(default_factory=dict)
    reranker_type: str = "token_overlap"
    reranker_model: str | None = None
    gold_evidence_version: str = "1"
    qdrant_collection_name: str


class IngestionSummary(BaseModel):
    document_id: str
    document_version_id: str
    chunk_count: int
    content_hash: str
    embedding_ms: int
    qdrant_ms: int
    completed_at: datetime
