from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine. The app creates it once at startup and disposes it at shutdown."""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory. `expire_on_commit=False`: objects stay readable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
