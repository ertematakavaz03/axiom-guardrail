from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from arq import create_pool
from arq.connections import RedisSettings
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from apps.api.app.config import get_settings
from apps.api.app.db.session import engine
from apps.api.app.main import app
from services.rag.storage import QdrantVectorStore

EXPECTED_TABLES = {
    "organizations",
    "users",
    "organization_members",
    "projects",
    "agents",
    "agent_versions",
    "test_suites",
    "scenarios",
    "runs",
    "case_results",
    "traces",
    "eval_results",
    "audit_logs",
    "corpora",
    "documents",
    "document_versions",
    "document_chunks",
    "rag_configs",
    "gold_evidence",
    "security_policies",
    "mcp_registrations",
}


@pytest_asyncio.fixture(scope="session", autouse=True)
async def isolated_database() -> None:
    database_url = get_settings().database_url
    if not (make_url(database_url).database or "").endswith("_test"):
        pytest.skip("Integration tests require a dedicated *_test database")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT 1"))
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            if not EXPECTED_TABLES.issubset(table_names):
                pytest.fail("All Alembic migrations must be applied to the test database")
            await connection.execute(
                text(f"TRUNCATE TABLE {', '.join(sorted(EXPECTED_TABLES))} CASCADE")
            )
    except OSError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {exc}")
    # The application engine is module-scoped while pytest-asyncio uses distinct fixture/test
    # loops. Drop setup connections before the first request so asyncpg never crosses loops.
    await engine.dispose()
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    settings = get_settings()
    try:
        # Direct-execution tests leave queued jobs behind. Give each test its own
        # queue so a burst worker cannot consume another test's expired jobs.
        redis = await create_pool(
            RedisSettings.from_dsn(settings.redis_url),
            default_queue_name=f"arq:test:{uuid.uuid4().hex}",
        )
        await redis.ping()
    except OSError as exc:
        pytest.skip(f"Redis is unavailable: {exc}")
    app.state.redis = redis
    qdrant = QdrantVectorStore(
        settings.qdrant_url,
        settings.qdrant_collection_prefix,
        settings.embedding_vector_size,
        settings.rag_retrieval_timeout_seconds,
    )
    try:
        await qdrant.health()
    except Exception as exc:
        await redis.aclose()
        pytest.skip(f"Qdrant is unavailable: {type(exc).__name__}")
    app.state.qdrant = qdrant
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        yield test_client
    await qdrant.close()
    await redis.aclose()
    await engine.dispose()
