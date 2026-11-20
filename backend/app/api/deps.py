"""FastAPI dependencies: database session and Keitaro client from the app state."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.keitaro.client import KeitaroClient


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A session per request. Routes commit explicitly; anything not committed is rolled back."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


def get_keitaro(request: Request) -> KeitaroClient:
    keitaro: KeitaroClient = request.app.state.keitaro
    return keitaro


SessionDep = Annotated[AsyncSession, Depends(get_session)]
KeitaroDep = Annotated[KeitaroClient, Depends(get_keitaro)]
