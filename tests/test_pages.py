import pytest

from tests.helpers import login

PROTECTED = ["/", "/docs", "/redoc"]


def test_login_page_is_open(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", PROTECTED)
def test_pages_send_signed_out_browsers_to_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path}"


@pytest.mark.parametrize("path", PROTECTED)
def test_pages_open_after_login(client, path):
    login(client)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_assistant_page_is_the_demo(client):
    login(client)
    assert "Ask about your transport policy" in client.get("/").text


def test_swagger_keeps_the_pasted_token(client):
    login(client)
    assert '"persistAuthorization": true' in client.get("/docs").text


def test_session_cookie_is_locked_down(client):
    cookie = login(client).headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie


def test_signing_out_closes_the_pages(client):
    token = login(client).json()["access_token"]
    client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_revoked_cookie_is_cleared_on_redirect(client):
    token = login(client).json()["access_token"]
    container = client.app.state.container
    container.auth.logout(container.auth.authenticate(token))
    # The browser still holds the cookie; the server refuses it and clears it.
    response = client.get("/docs", follow_redirects=False)
    assert response.status_code == 303
    assert 'cpa_session=""' in response.headers["set-cookie"]


def test_forged_cookie_is_refused(client):
    client.cookies.set("cpa_session", "abc.def.ghi")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_api_schema_stays_public(client):
    assert client.get("/openapi.json").status_code == 200
