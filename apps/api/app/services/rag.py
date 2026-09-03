from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.config import Settings
from apps.api.app.db.models import (
    Corpus,
    CorpusStatus,
    Document,
    DocumentChunk,
    DocumentSourceType,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    GoldEvidence,
    RagConfig,
    TestSuite,
    TrustLevel,
    User,
)
from apps.api.app.errors import ConflictError
from apps.api.app.repositories.scoping import (
    get_corpus,
    get_document,
    get_project,
    get_rag_config,
    get_scenario,
)
from apps.api.app.schemas.rag import (
    CorpusCreate,
    CorpusUpdate,
    DocumentCreate,
    GoldEvidenceCreate,
    RagConfigCreate,
)
from apps.api.app.services.audit import add_audit
from services.rag.chunking import ChunkingConfig, chunk_sections, parse_document
from services.rag.embeddings import embedding_provider_for
from services.rag.errors import DocumentIngestionError
from services.rag.models import RetrievalResult, RetrievalScope
from services.rag.reranking import reranker_for
from services.rag.retrieval import RetrievalService
from services.rag.sparse import sparse_vector
from services.rag.storage import QdrantVectorStore, collection_name_for


@dataclass
class IngestionResult:
    document: Document
    version: DocumentVersion
    chunks: list[DocumentChunk]
    qdrant_collection_name: str
    embedding_ms: int
    qdrant_ms: int


@dataclass(frozen=True)
class EffectiveRagConfig:
    embedding_provider: str
    embedding_model: str
    dense_enabled: bool
    sparse_enabled: bool
    top_k_dense: int
    top_k_sparse: int
    hybrid_top_k: int
    reranker_type: str
    rerank_top_n: int


class RagResourceService:
    def __init__(self, session: AsyncSession, user: User, settings: Settings) -> None:
        self.session = session
        self.user = user
        self.settings = settings

    async def list_corpora(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        await get_project(self.session, self.user.id, project_id)
        corpora = (
            await self.session.scalars(
                select(Corpus).where(Corpus.project_id == project_id).order_by(Corpus.created_at.desc())
            )
        ).all()
        result: list[dict[str, Any]] = []
        embedding_model = await self.session.scalar(
            select(RagConfig.embedding_model)
            .where(RagConfig.project_id == project_id)
            .order_by(RagConfig.created_at)
        )
        for corpus in corpora:
            document_count = await self.session.scalar(
                select(func.count(Document.id)).where(Document.corpus_id == corpus.id)
            )
            chunk_count = await self.session.scalar(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.corpus_id == corpus.id)
            )
            last_ingestion = await self.session.scalar(
                select(func.max(DocumentVersion.created_at))
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Document.corpus_id == corpus.id)
            )
            result.append(
                {
                    **{key: value for key, value in vars(corpus).items() if not key.startswith("_")},
                    "document_count": int(document_count or 0),
                    "chunk_count": int(chunk_count or 0),
                    "embedding_model": embedding_model or self.settings.embedding_model,
                    "last_ingestion": last_ingestion,
                }
            )
        return result

    async def create_corpus(self, project_id: uuid.UUID, payload: CorpusCreate) -> Corpus:
        project = await get_project(self.session, self.user.id, project_id)
        exists = await self.session.scalar(
            select(Corpus.id).where(
                Corpus.project_id == project.id,
                Corpus.name == payload.name,
                Corpus.version == payload.version,
            )
        )
        if exists:
            raise ConflictError("Corpus name and version already exist in this project")
        corpus = Corpus(project_id=project.id, **payload.model_dump())
        self.session.add(corpus)
        await self.session.flush()
        self._audit(project.organization_id, project.id, "corpus.create", "corpus", corpus.id)
        await self.session.commit()
        return corpus

    async def update_corpus(self, corpus_id: uuid.UUID, payload: CorpusUpdate) -> Corpus:
        corpus = await get_corpus(self.session, self.user.id, corpus_id)
        for key, value in payload.model_dump(exclude_none=True).items():
            setattr(corpus, key, value)
        project = await get_project(self.session, self.user.id, corpus.project_id)
        self._audit(project.organization_id, project.id, "corpus.update", "corpus", corpus.id)
        await self.session.commit()
        return corpus

    async def list_documents(self, corpus_id: uuid.UUID) -> Sequence[Document]:
        corpus = await get_corpus(self.session, self.user.id, corpus_id)
        return (
            await self.session.scalars(
                select(Document)
                .where(Document.corpus_id == corpus.id)
                .order_by(Document.created_at.desc())
            )
        ).all()

    async def create_document(self, corpus_id: uuid.UUID, payload: DocumentCreate) -> Document:
        corpus = await get_corpus(self.session, self.user.id, corpus_id)
        project = await get_project(self.session, self.user.id, corpus.project_id)
        name = sanitize_document_name(payload.name)
        exists = await self.session.scalar(
            select(Document.id).where(Document.corpus_id == corpus.id, Document.name == name)
        )
        if exists:
            raise ConflictError("Document already exists in this corpus")
        values = payload.model_dump()
        metadata = values.pop("metadata")
        values.pop("name")
        document = Document(
            corpus_id=corpus.id,
            project_id=project.id,
            name=name,
            document_metadata=metadata,
            **values,
        )
        self.session.add(document)
        await self.session.flush()
        self._audit(project.organization_id, project.id, "document.create", "document", document.id)
        await self.session.commit()
        return document

    async def list_chunks(self, document_id: uuid.UUID) -> Sequence[DocumentChunk]:
        document = await get_document(self.session, self.user.id, document_id)
        return (
            await self.session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.document_version_id, DocumentChunk.chunk_index)
            )
        ).all()

    async def list_configs(self, project_id: uuid.UUID) -> Sequence[RagConfig]:
        await get_project(self.session, self.user.id, project_id)
        return (
            await self.session.scalars(
                select(RagConfig)
                .where(RagConfig.project_id == project_id)
                .order_by(RagConfig.created_at)
            )
        ).all()

    async def create_config(self, project_id: uuid.UUID, payload: RagConfigCreate) -> RagConfig:
        project = await get_project(self.session, self.user.id, project_id)
        if await self.session.scalar(
            select(RagConfig.id).where(
                RagConfig.project_id == project.id, RagConfig.name == payload.name
            )
        ):
            raise ConflictError("RAG configuration name already exists")
        config = RagConfig(project_id=project.id, **payload.model_dump())
        self.session.add(config)
        await self.session.flush()
        self._audit(project.organization_id, project.id, "rag_config.create", "rag_config", config.id)
        await self.session.commit()
        return config

    async def list_gold_evidence(self, scenario_id: uuid.UUID) -> Sequence[GoldEvidence]:
        scenario = await get_scenario(self.session, self.user.id, scenario_id)
        return (
            await self.session.scalars(
                select(GoldEvidence)
                .where(GoldEvidence.scenario_id == scenario.id)
                .order_by(GoldEvidence.created_at)
            )
        ).all()

    async def create_gold_evidence(
        self, scenario_id: uuid.UUID, payload: GoldEvidenceCreate
    ) -> GoldEvidence:
        scenario = await get_scenario(self.session, self.user.id, scenario_id)
        suite = await self.session.get(TestSuite, scenario.test_suite_id)
        if suite is None:
            raise ConflictError("Scenario suite is missing")
        project = await get_project(self.session, self.user.id, suite.project_id)
        if payload.document_id:
            document = await get_document(self.session, self.user.id, payload.document_id)
            if document.project_id != project.id:
                raise ConflictError("Gold evidence document belongs to another project")
        if payload.chunk_id:
            chunk = await self.session.get(DocumentChunk, payload.chunk_id)
            if chunk is None or chunk.project_id != project.id:
                raise ConflictError("Gold evidence chunk belongs to another project")
            if payload.document_id and chunk.document_id != payload.document_id:
                raise ConflictError("Gold evidence chunk/document references do not match")
            if payload.document_version_id and chunk.document_version_id != payload.document_version_id:
                raise ConflictError("Gold evidence chunk/version references do not match")
        values = payload.model_dump()
        evidence_metadata = values.pop("metadata")
        evidence = GoldEvidence(
            scenario_id=scenario.id,
            evidence_metadata=evidence_metadata,
            **values,
        )
        self.session.add(evidence)
        await self.session.flush()
        self._audit(
            project.organization_id,
            project.id,
            "gold_evidence.create",
            "gold_evidence",
            evidence.id,
        )
        await self.session.commit()
        return evidence

    async def ingest_new_document(
        self,
        *,
        corpus_id: uuid.UUID,
        name: str,
        source_type: DocumentSourceType,
        content: bytes,
        mime_type: str | None,
        effective_date: datetime | None,
        trust_level: TrustLevel,
        metadata: dict[str, Any],
    ) -> IngestionResult:
        corpus = await get_corpus(self.session, self.user.id, corpus_id)
        document = await self.session.scalar(
            select(Document).where(
                Document.corpus_id == corpus.id, Document.name == sanitize_document_name(name)
            )
        )
        if document is None:
            document = await self.create_document(
                corpus.id,
                DocumentCreate(
                    name=name,
                    source_type=source_type,
                    mime_type=mime_type,
                    metadata=metadata,
                ),
            )
        return await self.ingest_version(
            document.id,
            content=content,
            source_type=source_type,
            mime_type=mime_type,
            effective_date=effective_date,
            trust_level=trust_level,
            metadata=metadata,
        )

    async def ingest_version(
        self,
        document_id: uuid.UUID,
        *,
        content: bytes,
        source_type: DocumentSourceType,
        mime_type: str | None,
        effective_date: datetime | None,
        trust_level: TrustLevel,
        metadata: dict[str, Any],
    ) -> IngestionResult:
        document = await get_document(self.session, self.user.id, document_id)
        corpus = await get_corpus(self.session, self.user.id, document.corpus_id)
        project = await get_project(self.session, self.user.id, document.project_id)
        if len(content) > self.settings.rag_max_upload_bytes:
            raise DocumentIngestionError(
                "Document exceeds configured upload size limit",
                details={"max_bytes": self.settings.rag_max_upload_bytes},
            )
        validate_content_signature(content, source_type, mime_type)
        sections = parse_document(content, source_type.value, mime_type)
        drafts = chunk_sections(
            sections,
            ChunkingConfig(
                chunk_size_tokens=self.settings.rag_chunk_size_tokens,
                overlap_tokens=self.settings.rag_chunk_overlap_tokens,
            ),
        )
        content_hash = hashlib.sha256(content).hexdigest()
        existing = await self.session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.content_hash == content_hash,
            )
        )
        if existing:
            chunks = list(
                (
                    await self.session.scalars(
                        select(DocumentChunk)
                        .where(DocumentChunk.document_version_id == existing.id)
                        .order_by(DocumentChunk.chunk_index)
                    )
                ).all()
            )
            return IngestionResult(
                document=document,
                version=existing,
                chunks=chunks,
                qdrant_collection_name=collection_name_for(
                    self.settings.qdrant_collection_prefix, str(project.id)
                ),
                embedding_ms=0,
                qdrant_ms=0,
            )
        version_number = document.current_version + 1
        version = DocumentVersion(
            document_id=document.id,
            version=version_number,
            content_hash=content_hash,
            content_length=len(content),
            effective_date=effective_date,
            trust_level=trust_level,
            status=DocumentVersionStatus.INDEXING,
            version_metadata=metadata,
        )
        document.status = DocumentStatus.INDEXING
        corpus.status = CorpusStatus.INDEXING
        self.session.add(version)
        await self.session.flush()
        chunks = [
            DocumentChunk(
                id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"agentarena:{document.id}:{content_hash}:{draft.chunk_index}",
                ),
                document_version_id=version.id,
                document_id=document.id,
                corpus_id=corpus.id,
                project_id=project.id,
                organization_id=project.organization_id,
                chunk_index=draft.chunk_index,
                text=draft.text,
                token_count=draft.token_count,
                page_number=draft.page_number,
                section_title=draft.section_title,
                qdrant_point_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"agentarena:{document.id}:{content_hash}:{draft.chunk_index}",
                    )
                ),
                chunk_metadata=draft.metadata,
            )
            for draft in drafts
        ]
        self.session.add_all(chunks)
        await self.session.commit()

        embedder = embedding_provider_for(self.settings)
        store = QdrantVectorStore(
            self.settings.qdrant_url,
            self.settings.qdrant_collection_prefix,
            self.settings.embedding_vector_size,
            self.settings.rag_retrieval_timeout_seconds,
        )
        try:
            embedding_started = time.perf_counter()
            dense_vectors = await embedder.embed_documents([chunk.text for chunk in chunks])
            embedding_ms = round((time.perf_counter() - embedding_started) * 1000)
            payloads = [
                {
                    "organization_id": str(project.organization_id),
                    "project_id": str(project.id),
                    "corpus_id": str(corpus.id),
                    "document_id": str(document.id),
                    "document_version_id": str(version.id),
                    "chunk_id": str(chunk.id),
                    "document_name": document.name,
                    "source_type": source_type.value,
                    "locale": metadata.get("locale"),
                    "effective_date": effective_date.isoformat() if effective_date else None,
                    "trust_level": trust_level.value,
                    "allowed_roles": metadata.get("allowed_roles", []),
                    "version": str(version_number),
                    "content": chunk.text,
                    "page_number": chunk.page_number,
                    "section_title": chunk.section_title,
                }
                for chunk in chunks
            ]
            qdrant_started = time.perf_counter()
            collection = await store.upsert(
                project_id=str(project.id),
                point_ids=[str(chunk.id) for chunk in chunks],
                dense_vectors=dense_vectors,
                sparse_vectors=[sparse_vector(chunk.text) for chunk in chunks],
                payloads=payloads,
            )
            qdrant_ms = round((time.perf_counter() - qdrant_started) * 1000)
        except Exception:
            version.status = DocumentVersionStatus.FAILED
            document.status = DocumentStatus.FAILED
            corpus.status = CorpusStatus.FAILED
            await self.session.commit()
            raise
        finally:
            await store.close()
        version.status = DocumentVersionStatus.READY
        document.status = DocumentStatus.READY
        document.current_version = version_number
        corpus.status = CorpusStatus.READY
        self._audit(
            project.organization_id,
            project.id,
            "document.ingest",
            "document_version",
            version.id,
        )
        await self.session.commit()
        # ``updated_at`` is generated by PostgreSQL on UPDATE and SQLAlchemy
        # expires that attribute.  Refresh while the async session is active so
        # FastAPI response serialization never attempts implicit async I/O.
        await self.session.refresh(document)
        return IngestionResult(document, version, chunks, collection, embedding_ms, qdrant_ms)

    async def retrieve(
        self,
        *,
        corpus_id: uuid.UUID,
        query: str,
        filters: dict[str, Any],
        top_k: int | None,
        rag_config_id: uuid.UUID | None,
    ) -> RetrievalResult:
        corpus = await get_corpus(self.session, self.user.id, corpus_id)
        project = await get_project(self.session, self.user.id, corpus.project_id)
        config = await self._effective_config(project.id, rag_config_id)
        store = QdrantVectorStore(
            self.settings.qdrant_url,
            self.settings.qdrant_collection_prefix,
            self.settings.embedding_vector_size,
            self.settings.rag_retrieval_timeout_seconds,
        )
        service = RetrievalService(
            store,
            embedding_provider_for(
                self.settings, config.embedding_provider, config.embedding_model
            ),
            reranker_for(config.reranker_type),
            self.settings.rag_retrieval_timeout_seconds,
        )
        try:
            return await service.retrieve(
                query=query,
                scope=RetrievalScope(
                    organization_id=str(project.organization_id),
                    project_id=str(project.id),
                    corpus_id=str(corpus.id),
                ),
                filters=filters,
                dense_enabled=config.dense_enabled,
                sparse_enabled=config.sparse_enabled,
                top_k_dense=config.top_k_dense,
                top_k_sparse=config.top_k_sparse,
                hybrid_top_k=top_k or config.hybrid_top_k,
                rerank_top_n=top_k or config.rerank_top_n,
            )
        finally:
            await store.close()

    async def _effective_config(
        self, project_id: uuid.UUID, rag_config_id: uuid.UUID | None
    ) -> EffectiveRagConfig:
        config: RagConfig | None
        if rag_config_id:
            config = await get_rag_config(self.session, self.user.id, rag_config_id)
            if config.project_id != project_id:
                raise ConflictError("RAG configuration and corpus must belong to the same project")
        else:
            config = await self.session.scalar(
                select(RagConfig)
                .where(RagConfig.project_id == project_id)
                .order_by(RagConfig.created_at)
            )
        if config:
            return EffectiveRagConfig(
                embedding_provider=config.embedding_provider,
                embedding_model=config.embedding_model,
                dense_enabled=config.dense_enabled,
                sparse_enabled=config.sparse_enabled,
                top_k_dense=config.top_k_dense,
                top_k_sparse=config.top_k_sparse,
                hybrid_top_k=config.hybrid_top_k,
                reranker_type=config.reranker_type,
                rerank_top_n=config.rerank_top_n,
            )
        return EffectiveRagConfig(
            embedding_provider=self.settings.embedding_provider,
            embedding_model=self.settings.embedding_model,
            dense_enabled=True,
            sparse_enabled=True,
            top_k_dense=20,
            top_k_sparse=20,
            hybrid_top_k=10,
            reranker_type="token_overlap",
            rerank_top_n=5,
        )

    def _audit(
        self,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
    ) -> None:
        add_audit(
            self.session,
            organization_id=organization_id,
            user_id=self.user.id,
            project_id=project_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )


def sanitize_document_name(name: str) -> str:
    normalized = name.replace("\\", "/").split("/")[-1]
    sanitized = re.sub(r"[^A-Za-z0-9._ -]", "_", normalized).strip(" .")
    if not sanitized or sanitized in {".", ".."}:
        raise DocumentIngestionError("Document filename is invalid")
    return sanitized[:255]


def validate_content_signature(
    content: bytes, source_type: DocumentSourceType, mime_type: str | None
) -> None:
    expected_mime = {
        DocumentSourceType.TEXT: {None, "text/plain"},
        DocumentSourceType.MARKDOWN: {None, "text/markdown", "text/plain"},
        DocumentSourceType.PDF: {None, "application/pdf"},
    }
    if mime_type not in expected_mime[source_type]:
        raise DocumentIngestionError(
            "MIME type does not match the declared document type",
            details={"source_type": source_type.value, "mime_type": mime_type},
        )
    if source_type == DocumentSourceType.PDF and not content.startswith(b"%PDF-"):
        raise DocumentIngestionError("Uploaded PDF does not have a valid PDF signature")
    if source_type != DocumentSourceType.PDF and (b"\x00" in content[:4096]):
        raise DocumentIngestionError("Text upload appears to contain binary content")
