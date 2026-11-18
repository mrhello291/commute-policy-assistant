import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_missing_token_gives_plain_401(client):
    response = client.get("/v1/me")
    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "unauthorized"
    assert "sign in again" in body["message"]
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_forged_token_gives_plain_401(client):
    response = client.get("/v1/me", headers={"Authorization": "Bearer abc.def.ghi"})
    assert response.status_code == 401
    assert "abc" not in response.text


def test_validation_error_lists_fields_without_echoing_input(client):
    response = client.post("/v1/auth/login", json={"employee_id": "", "company_id": "<script>"})
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "invalid_request"
    assert any(p["field"] == "employee_id" for p in body["problems"])
    assert "<script>" not in response.text


@pytest.fixture
def exploding_client(settings):
    app = create_app(settings)

    @app.get("/boom")
    def boom():
        raise RuntimeError("database password is hunter2")

    return TestClient(app, raise_server_exceptions=False)


def test_unexpected_error_hides_details(exploding_client):
    response = exploding_client.get("/boom")
    assert response.status_code == 500
    assert response.json()["status"] == "error"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
