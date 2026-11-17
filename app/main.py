"""Application entry point.

`create_app()` builds the FastAPI app. Keeping construction in a factory lets
tests spin up isolated instances instead of sharing a module-level global.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Commute Policy Assistant", version="0.1.0")

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
