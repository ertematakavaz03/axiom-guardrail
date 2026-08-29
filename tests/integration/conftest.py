from __future__ import annotations

import os

import pytest
import pytest_asyncio
from arq import create_pool
from arq.connections import RedisSettings
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, text

from apps.api.app.config import get_settings
from apps.api.app.db.session import engine
from apps.api.app.main import app

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
}


@pytest_asyncio.fixture(scope="session", autouse=True)
async def isolated_database() -> None:
    database_url = get_settings().database_url
    if "_test" not in database_url and os.getenv("AGENTARENA_ALLOW_TEST_DATABASE") != "1":
        pytest.skip("Integration tests require a dedicated *_test database")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT 1"))
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            if not EXPECTED_TABLES.issubset(table_names):
                pytest.fail("Phase 1 Alembic migration has not been applied to the test database")
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
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.ping()
    except OSError as exc:
        pytest.skip(f"Redis is unavailable: {exc}")
    app.state.redis = redis
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        yield test_client
    await redis.aclose()
    await engine.dispose()
