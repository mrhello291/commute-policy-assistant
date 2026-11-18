"""The web pages: sign-in, the demo assistant, and the API docs.

`/login` is open to everyone. The other pages need a signed-in browser,
which is checked through the session cookie set at login; without a valid
one the browser is sent to `/login`, which then brings it back. The cookie
only opens pages. API calls made from them still send the bearer token.
"""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.api.deps import get_container
from app.api.routes_auth import SESSION_COOKIE
from app.container import Container
from app.errors import AuthError

STATIC = Path(__file__).resolve().parent.parent.parent / "static"
# Pages a sign-in may return to. Anything else goes to the assistant, so the
# `next` parameter can't be used to send someone to another site.
RETURN_PAGES = {"/", "/docs", "/redoc"}
NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter(include_in_schema=False)


def signed_in(request: Request, container: Container) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        container.auth.authenticate(token)
    except AuthError:
        return False
    return True


def to_login(path: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?next={quote(path)}", status_code=303, headers=NO_STORE)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/login")
def login_page() -> FileResponse:
    return FileResponse(STATIC / "login.html", headers=NO_STORE)


@router.get("/")
def assistant_page(request: Request, container: Container = Depends(get_container)) -> Response:
    if not signed_in(request, container):
        return to_login("/")
    return FileResponse(STATIC / "index.html", headers=NO_STORE)


@router.get("/docs")
def swagger_page(request: Request, container: Container = Depends(get_container)) -> Response:
    if not signed_in(request, container):
        return to_login("/docs")
    page = get_swagger_ui_html(
        openapi_url=request.app.openapi_url,
        title=f"{request.app.title} - API docs",
        # Keep the pasted token across page reloads.
        swagger_ui_parameters={"persistAuthorization": True},
    )
    page.headers.update(NO_STORE)
    return page


@router.get("/redoc")
def redoc_page(request: Request, container: Container = Depends(get_container)) -> Response:
    if not signed_in(request, container):
        return to_login("/redoc")
    page = get_redoc_html(openapi_url=request.app.openapi_url, title=f"{request.app.title} - API reference")
    page.headers.update(NO_STORE)
    return page
