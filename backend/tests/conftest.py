import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.meeting import Meeting


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    configured_url = os.getenv("TEST_DATABASE_URL")
    if configured_url:
        yield configured_url
        return

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest_asyncio.fixture(scope="session")
async def test_session_factory(
    database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield session_factory

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def clean_database(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    async with test_session_factory() as session:
        await session.execute(delete(Meeting))
        await session.commit()
    yield


@pytest_asyncio.fixture
async def client(
    test_session_factory: async_sessionmaker[AsyncSession],
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
