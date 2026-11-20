"""Application entry point.

`create_app()` builds the FastAPI app. Keeping construction in a factory lets
tests spin up isolated instances instead of sharing a module-level global;
`serve()` is what uvicorn calls in production.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import error_handlers, routes_ask, routes_auth, routes_dev, routes_pages, routes_policies
from app.config import Settings, get_settings
from app.container import Container


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or Container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        container.startup()
        try:
            yield
        finally:
            container.close()

    # The docs pages are served by routes_pages, behind sign-in.
    app = FastAPI(title="Commute Policy Assistant", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.container = container
    error_handlers.install(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(routes_pages.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_ask.router)
    app.include_router(routes_policies.router)
    if settings.is_dev:
        app.include_router(routes_dev.router)

    return app


def serve() -> FastAPI:
    """Entry point for uvicorn: `uvicorn app.main:serve --factory`.

    Building the app only when the server starts (instead of at import)
    keeps tests from loading the embedding model or reading `.env` just by
    importing this module.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
