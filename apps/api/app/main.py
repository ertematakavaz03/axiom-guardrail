from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from apps.api.app.api.v1 import router as v1_router
from apps.api.app.config import get_settings
from apps.api.app.db.session import SessionLocal
from apps.api.app.errors import (
    AgentArenaError,
    ConflictError,
    QueueUnavailableError,
    ResourceNotFoundError,
)
from apps.api.app.logging import configure_logging
from services.rag.storage import QdrantVectorStore

settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.qdrant = QdrantVectorStore(
        settings.qdrant_url,
        settings.qdrant_collection_prefix,
        settings.embedding_vector_size,
        settings.rag_retrieval_timeout_seconds,
    )
    yield
    await app.state.qdrant.close()
    await app.state.redis.aclose()


app = FastAPI(
    title="Axiom Guardrail API",
    version="0.1.0",
    description="Evidence-backed AI agent quality and security evaluation API.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(v1_router)


@app.middleware("http")
async def request_context(request: Request, call_next: object) -> object:
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    response = await call_next(request)  # type: ignore[operator]
    response.headers["x-request-id"] = request_id
    logger.info(
        "request.completed",
        extra={"request_id": request_id, "method": request.method, "path": request.url.path},
    )
    return response


@app.exception_handler(AgentArenaError)
async def application_error(_: Request, exc: AgentArenaError) -> JSONResponse:
    code = status.HTTP_400_BAD_REQUEST
    if isinstance(exc, ResourceNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, ConflictError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, QueueUnavailableError):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=code,
        content={"detail": {"reason_code": exc.reason_code, "message": exc.message}},
    )


@app.get("/livez", tags=["System"])
async def livez() -> dict[str, str]:
    """Liveness: is this process able to serve at all?

    Deliberately checks nothing external. An orchestrator uses liveness to decide whether
    to *restart* the container, and restarting the API because Postgres blinked turns a
    dependency hiccup into a rolling outage. Readiness is the probe that should fail then.
    """
    return {"status": "ok"}


@app.get("/health", tags=["System"])
async def health(request: Request) -> dict[str, str]:
    """Combined check, kept at its original path for existing callers and compose."""
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    await request.app.state.redis.ping()
    await request.app.state.qdrant.health()
    return {"status": "ok", "postgres": "ok", "redis": "ok", "qdrant": "ok"}


@app.get("/readyz", tags=["System"])
async def readyz(request: Request) -> dict[str, str]:
    """Readiness: can this process serve traffic right now?

    Fails while a backing service is unreachable, so the instance is taken out of the
    load-balancer rotation without being killed.
    """
    return await health(request)
