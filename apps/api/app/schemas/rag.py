from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from apps.api.app.db.models import (
    CorpusStatus,
    DocumentSourceType,
    DocumentStatus,
    DocumentVersionStatus,
    TrustLevel,
)
from apps.api.app.schemas.common import ORMModel
from services.rag.models import RetrievalHit, RetrievalTimings


class CorpusCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    version: str = Field(default="1", min_length=1, max_length=100)


class CorpusUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: CorpusStatus | None = None


class CorpusResponse(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    version: str
    status: CorpusStatus
    created_at: datetime
    updated_at: datetime


class CorpusSummaryResponse(CorpusResponse):
    document_count: int = 0
    chunk_count: int = 0
    embedding_model: str | None = None
    last_ingestion: datetime | None = None


class DocumentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_type: DocumentSourceType
    mime_type: str | None = Field(default=None, max_length=200)
    source_uri: str | None = Field(default=None, max_length=2048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(ORMModel):
    id: uuid.UUID
    corpus_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    source_type: DocumentSourceType
    mime_type: str | None
    source_uri: str | None
    current_version: int
    status: DocumentStatus
    metadata: dict[str, Any] = Field(validation_alias="document_metadata")
    created_at: datetime
    updated_at: datetime


class DocumentVersionCreate(BaseModel):
    content: str = Field(min_length=1)
    source_type: DocumentSourceType | None = None
    mime_type: str | None = Field(default=None, max_length=200)
    effective_date: datetime | None = None
    trust_level: TrustLevel = TrustLevel.STANDARD
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentVersionResponse(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    content_hash: str
    content_length: int
    effective_date: datetime | None
    trust_level: TrustLevel
    status: DocumentVersionStatus
    metadata: dict[str, Any] = Field(validation_alias="version_metadata")
    created_at: datetime


class DocumentChunkResponse(ORMModel):
    id: uuid.UUID
    document_version_id: uuid.UUID
    document_id: uuid.UUID
    corpus_id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    chunk_index: int
    text: str
    token_count: int | None
    page_number: int | None
    section_title: str | None
    qdrant_point_id: str | None
    metadata: dict[str, Any] = Field(validation_alias="chunk_metadata")
    created_at: datetime


class RagConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    embedding_provider: str = Field(default="deterministic", max_length=100)
    embedding_model: str = Field(default="deterministic-hash-v1", max_length=200)
    dense_enabled: bool = True
    sparse_enabled: bool = True
    top_k_dense: int = Field(default=20, ge=1, le=100)
    top_k_sparse: int = Field(default=20, ge=1, le=100)
    hybrid_top_k: int = Field(default=10, ge=1, le=100)
    reranker_type: str = Field(default="token_overlap", max_length=100)
    reranker_model: str | None = Field(default=None, max_length=200)
    rerank_top_n: int = Field(default=5, ge=1, le=50)
    metadata_filter_policy: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sparse_enabled")
    @classmethod
    def at_least_one_retrieval_mode(cls, value: bool, info: Any) -> bool:
        if not value and info.data.get("dense_enabled") is False:
            raise ValueError("At least one retrieval mode must be enabled")
        return value


class RagConfigResponse(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    embedding_provider: str
    embedding_model: str
    dense_enabled: bool
    sparse_enabled: bool
    top_k_dense: int
    top_k_sparse: int
    hybrid_top_k: int
    reranker_type: str
    reranker_model: str | None
    rerank_top_n: int
    metadata_filter_policy: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IngestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_type: DocumentSourceType
    content: str = Field(min_length=1)
    mime_type: str | None = Field(default=None, max_length=200)
    effective_date: datetime | None = None
    trust_level: TrustLevel = TrustLevel.STANDARD
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    document: DocumentResponse
    version: DocumentVersionResponse
    chunks: list[DocumentChunkResponse]
    qdrant_collection_name: str
    embedding_ms: int
    qdrant_ms: int


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int | None = Field(default=None, ge=1, le=50)
    rag_config_id: uuid.UUID | None = None


class RetrievalResponse(BaseModel):
    query: str
    hits: list[RetrievalHit]
    dense_hits: list[RetrievalHit]
    sparse_hits: list[RetrievalHit]
    candidate_count: int
    timings: RetrievalTimings


class GoldEvidenceCreate(BaseModel):
    document_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    relevance_score: float = Field(default=1.0, ge=0, le=10)
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("chunk_id")
    @classmethod
    def at_least_one_evidence_reference(
        cls, value: uuid.UUID | None, info: Any
    ) -> uuid.UUID | None:
        if (
            value is None
            and info.data.get("document_id") is None
            and info.data.get("document_version_id") is None
        ):
            raise ValueError("At least one evidence reference is required")
        return value


class GoldEvidenceResponse(ORMModel):
    id: uuid.UUID
    scenario_id: uuid.UUID
    document_id: uuid.UUID | None
    document_version_id: uuid.UUID | None
    chunk_id: uuid.UUID | None
    relevance_score: float
    required: bool
    metadata: dict[str, Any] = Field(validation_alias="evidence_metadata")
    created_at: datetime
