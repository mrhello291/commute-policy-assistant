"""Turns exceptions into clean JSON responses.

No handler ever includes exception text or a traceback in the body. Details
go to the server log alongside the request id, which is also returned to the
client so a support conversation can find the matching log line.
"""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import messages
from app.errors import AuthError, PermissionDenied

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_body(request: Request, status: str, message: str) -> dict:
    return {"status": status, "message": message, "request_id": request_id(request)}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def assign_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        try:
            response = await call_next(request)
        except Exception:
            # Raised past every handler; still answer in our own format.
            log.exception("unhandled error request_id=%s", request.state.request_id)
            response = JSONResponse(
                error_body(request, "error", messages.UNEXPECTED), status_code=500
            )
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    @app.exception_handler(AuthError)
    async def on_auth_error(request: Request, exc: AuthError):
        log.info("auth rejected request_id=%s reason=%s", request_id(request), exc)
        return JSONResponse(
            error_body(request, "unauthorized", messages.SESSION_INVALID),
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(PermissionDenied)
    async def on_permission_denied(request: Request, exc: PermissionDenied):
        log.info("permission denied request_id=%s reason=%s", request_id(request), exc)
        return JSONResponse(error_body(request, "forbidden", messages.NOT_ALLOWED), status_code=403)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        # Field names and reasons are safe to show and help the caller fix
        # the request; the raw input values are not echoed back.
        problems = [
            {"field": ".".join(str(p) for p in err["loc"][1:]), "problem": err["msg"]}
            for err in exc.errors()
        ]
        body = error_body(request, "invalid_request", messages.BAD_REQUEST)
        body["problems"] = problems
        return JSONResponse(body, status_code=422)

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):
        log.exception("unexpected error request_id=%s", request_id(request))
        return JSONResponse(error_body(request, "error", messages.UNEXPECTED), status_code=500)
