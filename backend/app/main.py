"""FastAPI application: lifespan resources, routes and error mapping.

Run: `uv run uvicorn app.main:app --reload` from `backend/`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_sessionmaker
from app.keitaro.client import KeitaroClient, KeitaroError, build_http_client
from app.services.creator import CreatorError
from app.services.editor import EditorError, NotFoundError
from app.services.sync import UnpushedChangesError

KEITARO_NOT_FOUND: Final[int] = 404


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database engine and the Keitaro HTTP client once, close them on shutdown."""
    settings: Settings = get_settings()
    engine = build_engine(settings)
    app.state.sessionmaker = build_sessionmaker(engine)
    async with build_http_client(settings) as http:
        app.state.keitaro = KeitaroClient(http)
        yield
    await engine.dispose()


def create_app() -> FastAPI:
    app: FastAPI = FastAPI(title="Keitaro Wrapper", lifespan=lifespan)
    app.include_router(router)
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(EditorError, _conflict)
    app.add_exception_handler(CreatorError, _conflict)
    app.add_exception_handler(UnpushedChangesError, _conflict)
    app.add_exception_handler(KeitaroError, _keitaro_failed)
    return app


async def _not_found(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


async def _conflict(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


async def _keitaro_failed(_: Request, exc: Exception) -> JSONResponse:
    """Keitaro's 404 stays 404 (no such campaign there); any other failure is a bad gateway."""
    assert isinstance(exc, KeitaroError)
    if exc.status_code == KEITARO_NOT_FOUND:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"not found in Keitaro: {exc.message}"},
        )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"Keitaro request failed: {exc.message}"},
    )


app: FastAPI = create_app()
