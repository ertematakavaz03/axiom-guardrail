from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.config import Settings, get_settings
from apps.api.app.db.models import DocumentSourceType, Trace, TrustLevel, User
from apps.api.app.db.session import get_session
from apps.api.app.errors import ConflictError
from apps.api.app.repositories.scoping import get_case, get_corpus, get_document
from apps.api.app.schemas.rag import (
    CorpusCreate,
    CorpusResponse,
    CorpusSummaryResponse,
    CorpusUpdate,
    DocumentChunkResponse,
    DocumentCreate,
    DocumentResponse,
    DocumentVersionCreate,
    GoldEvidenceCreate,
    GoldEvidenceResponse,
    IngestRequest,
    IngestResponse,
    RagConfigCreate,
    RagConfigResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from apps.api.app.schemas.resources import TraceResponse
from apps.api.app.security.auth import get_current_user
from apps.api.app.services.rag import RagResourceService

router = APIRouter(tags=["RAG corpora and evidence"])


def service(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RagResourceService:
    return RagResourceService(session, user, settings)


@router.get("/projects/{project_id}/corpora", response_model=list[CorpusSummaryResponse])
async def list_corpora(project_id: uuid.UUID, rag: RagResourceService = Depends(service)) -> object:
    return await rag.list_corpora(project_id)


@router.post(
    "/projects/{project_id}/corpora",
    response_model=CorpusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_corpus(
    project_id: uuid.UUID,
    payload: CorpusCreate,
    rag: RagResourceService = Depends(service),
) -> object:
    return await rag.create_corpus(project_id, payload)


@router.get("/corpora/{corpus_id}", response_model=CorpusResponse)
async def corpus_detail(
    corpus_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await get_corpus(session, user.id, corpus_id)


@router.patch("/corpora/{corpus_id}", response_model=CorpusResponse)
async def update_corpus(
    corpus_id: uuid.UUID,
    payload: CorpusUpdate,
    rag: RagResourceService = Depends(service),
) -> object:
    return await rag.update_corpus(corpus_id, payload)


@router.get("/corpora/{corpus_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    corpus_id: uuid.UUID, rag: RagResourceService = Depends(service)
) -> object:
    return await rag.list_documents(corpus_id)


@router.post(
    "/corpora/{corpus_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    corpus_id: uuid.UUID,
    payload: DocumentCreate,
    rag: RagResourceService = Depends(service),
) -> object:
    return await rag.create_document(corpus_id, payload)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def document_detail(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    return await get_document(session, user.id, document_id)


@router.get("/documents/{document_id}/chunks", response_model=list[DocumentChunkResponse])
async def document_chunks(
    document_id: uuid.UUID, rag: RagResourceService = Depends(service)
) -> object:
    return await rag.list_chunks(document_id)


@router.post(
    "/documents/{document_id}/versions",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_version(
    document_id: uuid.UUID,
    payload: DocumentVersionCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    rag: RagResourceService = Depends(service),
) -> object:
    document = await get_document(session, user.id, document_id)
    result = await rag.ingest_version(
        document.id,
        content=payload.content.encode(),
        source_type=payload.source_type or document.source_type,
        mime_type=payload.mime_type or document.mime_type,
        effective_date=payload.effective_date,
        trust_level=payload.trust_level,
        metadata=payload.metadata,
    )
    return _ingestion_response(result)


@router.post(
    "/corpora/{corpus_id}/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    corpus_id: uuid.UUID,
    payload: IngestRequest,
    rag: RagResourceService = Depends(service),
) -> object:
    result = await rag.ingest_new_document(
        corpus_id=corpus_id,
        name=payload.name,
        source_type=payload.source_type,
        content=payload.content.encode(),
        mime_type=payload.mime_type,
        effective_date=payload.effective_date,
        trust_level=payload.trust_level,
        metadata=payload.metadata,
    )
    return _ingestion_response(result)


@router.post(
    "/corpora/{corpus_id}/upload",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    corpus_id: uuid.UUID,
    file: UploadFile = File(...),
    source_type: DocumentSourceType = Form(...),
    effective_date: datetime | None = Form(default=None),
    trust_level: TrustLevel = Form(default=TrustLevel.STANDARD),
    metadata: str = Form(default="{}"),
    settings: Settings = Depends(get_settings),
    rag: RagResourceService = Depends(service),
) -> object:
    content = await file.read(settings.rag_max_upload_bytes + 1)
    try:
        parsed_metadata = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise ConflictError("Upload metadata must be valid JSON") from exc
    if not isinstance(parsed_metadata, dict):
        raise ConflictError("Upload metadata must be a JSON object")
    result = await rag.ingest_new_document(
        corpus_id=corpus_id,
        name=file.filename or "document",
        source_type=source_type,
        content=content,
        mime_type=file.content_type,
        effective_date=effective_date,
        trust_level=trust_level,
        metadata=parsed_metadata,
    )
    return _ingestion_response(result)


@router.get("/projects/{project_id}/rag-configs", response_model=list[RagConfigResponse])
async def list_rag_configs(
    project_id: uuid.UUID, rag: RagResourceService = Depends(service)
) -> object:
    return await rag.list_configs(project_id)


@router.post(
    "/projects/{project_id}/rag-configs",
    response_model=RagConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_rag_config(
    project_id: uuid.UUID,
    payload: RagConfigCreate,
    rag: RagResourceService = Depends(service),
) -> object:
    return await rag.create_config(project_id, payload)


@router.post("/corpora/{corpus_id}/retrieve", response_model=RetrievalResponse)
async def retrieve(
    corpus_id: uuid.UUID,
    payload: RetrievalRequest,
    rag: RagResourceService = Depends(service),
) -> object:
    return await rag.retrieve(
        corpus_id=corpus_id,
        query=payload.query,
        filters=payload.filters,
        top_k=payload.top_k,
        rag_config_id=payload.rag_config_id,
    )


@router.get("/scenarios/{scenario_id}/gold-evidence", response_model=list[GoldEvidenceResponse])
async def list_gold_evidence(
    scenario_id: uuid.UUID, rag: RagResourceService = Depends(service)
) -> object:
    return await rag.list_gold_evidence(scenario_id)


@router.post(
    "/scenarios/{scenario_id}/gold-evidence",
    response_model=GoldEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_gold_evidence(
    scenario_id: uuid.UUID,
    payload: GoldEvidenceCreate,
    rag: RagResourceService = Depends(service),
) -> object:
    return await rag.create_gold_evidence(scenario_id, payload)


@router.get("/cases/{case_result_id}/retrieval", response_model=list[TraceResponse])
async def case_retrieval(
    case_result_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    case = await get_case(session, user.id, case_result_id)
    return await _case_events(session, case.id, prefix="retrieval")


@router.get("/cases/{case_result_id}/claims", response_model=list[TraceResponse])
async def case_claims(
    case_result_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    case = await get_case(session, user.id, case_result_id)
    return await _case_events(session, case.id, exact="claim_extracted")


@router.get("/cases/{case_result_id}/citations", response_model=list[TraceResponse])
async def case_citations(
    case_result_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> object:
    case = await get_case(session, user.id, case_result_id)
    return await _case_events(session, case.id, prefix="citation")


async def _case_events(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    prefix: str | None = None,
    exact: str | None = None,
) -> list[Trace]:
    query = select(Trace).where(Trace.case_result_id == case_id)
    if exact:
        query = query.where(Trace.event_type == exact)
    elif prefix:
        query = query.where(Trace.event_type.startswith(prefix))
    return list((await session.scalars(query.order_by(Trace.sequence_number))).all())


def _ingestion_response(result: Any) -> dict[str, Any]:
    return {
        "document": result.document,
        "version": result.version,
        "chunks": result.chunks,
        "qdrant_collection_name": result.qdrant_collection_name,
        "embedding_ms": result.embedding_ms,
        "qdrant_ms": result.qdrant_ms,
    }
