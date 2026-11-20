"""Shared fixtures: a real PostgreSQL test database.

Tests that need the database use the `session` fixture. The database `keitaro_wrapper_test` lives in
the same dev container as the app database (compose.dev.yaml) and is created on the first run. Its
tables are recreated from the models once per test run; each test runs inside a transaction that is
rolled back, so tests do not see each other's data. When PostgreSQL is not reachable, these tests
are skipped instead of failing.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Final

import pytest
from sqlalchemy import URL, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models import Base

TEST_DATABASE_NAME: Final[str] = "keitaro_wrapper_test"


async def _prepare_test_database(url: URL) -> None:
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with admin.connect() as connection:
            exists: object = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DATABASE_NAME},
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE_NAME}"'))
    finally:
        await admin.dispose()

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    url: URL = get_settings().database_url.set(database=TEST_DATABASE_NAME)
    try:
        asyncio.run(_prepare_test_database(url))
    except (OSError, SQLAlchemyError) as exc:
        pytest.skip(f"PostgreSQL test database is not available: {type(exc).__name__}")
    return url


@pytest.fixture
async def session(test_database_url: URL) -> AsyncIterator[AsyncSession]:
    """Session inside an outer transaction that is rolled back after the test.

    `create_savepoint`: a `commit()` in the code under test only releases a savepoint, the outer
    transaction still rolls everything back.
    """
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        test_session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield test_session
        finally:
            await test_session.close()
            await transaction.rollback()
    await engine.dispose()
