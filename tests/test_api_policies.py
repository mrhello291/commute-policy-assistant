from tests.conftest import POLICY_DIR
from tests.helpers import auth

YAMUNA_V2 = (POLICY_DIR / "yamuna" / "v2.md").read_text()


def yamuna_v3():
    return YAMUNA_V2.replace("version: 2", "version: 3").replace("INR 900", "INR 950")


def test_employee_sees_their_own_live_policy(client):
    body = client.get("/v1/policies/active", headers=auth(client, "gomti")).json()
    assert body == {
        "company_id": "gomti",
        "company_name": "Gomti Logistics",
        "title": "Gomti Logistics Commute Policy",
        "version": 1,
    }


def test_admin_uploads_new_version(client):
    response = client.post("/v1/policies", json={"content": yamuna_v3()}, headers=auth(client, role="policy_admin"))
    assert response.status_code == 200
    assert response.json()["activated"] and response.json()["previous_version"] == 2
    live = client.get("/v1/policies/active", headers=auth(client)).json()
    assert live["version"] == 3


def test_employee_cannot_upload(client):
    response = client.post("/v1/policies", json={"content": yamuna_v3()}, headers=auth(client))
    assert response.status_code == 403


def test_admin_cannot_upload_for_another_company(client):
    headers = auth(client, "gomti", role="policy_admin")
    response = client.post("/v1/policies", json={"content": yamuna_v3()}, headers=headers)
    assert response.status_code == 403
    assert client.get("/v1/policies/active", headers=auth(client)).json()["version"] == 2


def test_old_version_upload_is_a_conflict(client):
    old = (POLICY_DIR / "yamuna" / "v1.md").read_text()
    response = client.post("/v1/policies", json={"content": old}, headers=auth(client, role="policy_admin"))
    assert response.status_code == 409
    assert "Nothing was changed" in response.json()["message"]


def test_malformed_upload_explains_why(client):
    response = client.post(
        "/v1/policies", json={"content": "just some text"}, headers=auth(client, role="policy_admin")
    )
    assert response.status_code == 422
    assert "front matter" in response.json()["message"]


def test_failed_ingest_reports_that_old_version_is_kept(client, monkeypatch):
    container = client.app.state.container

    def broken(_texts):
        raise ConnectionError("embedder down")

    monkeypatch.setattr(container.embedder, "embed", broken)
    response = client.post("/v1/policies", json={"content": yamuna_v3()}, headers=auth(client, role="policy_admin"))
    assert response.status_code == 503
    assert "previous version is still the one in use" in response.json()["message"]
    assert "embedder down" not in response.text
    assert client.get("/v1/policies/active", headers=auth(client)).json()["version"] == 2
