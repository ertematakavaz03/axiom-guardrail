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

settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.redis.aclose()


app = FastAPI(
    title="AgentArena API",
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


@app.get("/health", tags=["System"])
async def health(request: Request) -> dict[str, str]:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    await request.app.state.redis.ping()
    return {"status": "ok"}
