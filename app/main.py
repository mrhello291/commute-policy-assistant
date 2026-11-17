"""Application entry point.

`create_app()` builds the FastAPI app. Keeping construction in a factory lets
tests spin up isolated instances instead of sharing a module-level global.
"""

from fastapi import Depends, FastAPI

from app.api import routes_dev
from app.api.deps import get_principal
from app.auth import Principal
from app.config import Settings, get_settings
from app.container import Container


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    container = container or Container(settings)

    app = FastAPI(title="Commute Policy Assistant", version="0.1.0")
    app.state.container = container

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/me", tags=["auth"])
    def whoami(principal: Principal = Depends(get_principal)) -> dict[str, str]:
        return {"subject": principal.subject, "company_id": principal.company_id, "role": principal.role}

    if settings.is_dev:
        app.include_router(routes_dev.router)

    return app


app = create_app()
