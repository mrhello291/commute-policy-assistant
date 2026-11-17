"""Application entry point.

`create_app()` builds the FastAPI app. Keeping construction in a factory lets
tests spin up isolated instances instead of sharing a module-level global.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api import error_handlers, routes_dev, routes_policies
from app.api.deps import get_principal
from app.auth import Principal
from app.config import Settings, get_settings
from app.container import Container


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or Container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        container.startup()
        yield

    app = FastAPI(title="Commute Policy Assistant", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    error_handlers.install(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/me", tags=["auth"])
    def whoami(principal: Principal = Depends(get_principal)) -> dict[str, str]:
        return {"subject": principal.subject, "company_id": principal.company_id, "role": principal.role}

    app.include_router(routes_policies.router)
    if settings.is_dev:
        app.include_router(routes_dev.router)

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
