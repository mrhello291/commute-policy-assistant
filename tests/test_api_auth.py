from app.config import Environment, Settings
from app.main import create_app
from fastapi.testclient import TestClient


def _token(client, company="arcadia", role="employee"):
    response = client.post("/dev/token", json={"subject": "emp-7", "company_id": company, "role": role})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_identity_comes_from_token(client):
    token = _token(client, "bluepeak")
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.json() == {"subject": "emp-7", "company_id": "bluepeak", "role": "employee"}


def test_dev_token_endpoint_rejects_unknown_fields(client):
    response = client.post("/dev/token", json={"subject": "x", "company_id": "a", "is_admin": True})
    assert response.status_code == 422


def test_dev_token_endpoint_absent_outside_dev(tmp_path):
    settings = Settings(_env_file=None, app_env=Environment.PROD, data_dir=tmp_path,
                        jwt_secret="test-secret-that-is-at-least-32-bytes-long")
    client = TestClient(create_app(settings))
    assert client.post("/dev/token", json={"subject": "x", "company_id": "a"}).status_code == 404
