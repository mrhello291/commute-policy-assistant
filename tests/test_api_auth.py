import json
import os

from fastapi.testclient import TestClient

from app.config import Environment
from app.main import create_app
from tests.helpers import auth, login, token


def test_login_returns_token_employee_and_company(client):
    response = login(client, "yamuna", "YAM-1004")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["employee"] == {"employee_id": "YAM-1004", "name": "Vikram Shetty", "role": "policy_admin"}
    assert body["company"] == {"company_id": "yamuna", "company_name": "Yamuna Software Services"}
    assert 0 < body["expires_in"] <= 480 * 60
    assert response.headers["Cache-Control"] == "no-store"


def test_identity_comes_from_token(client):
    me = client.get("/v1/me", headers=auth(client, "gomti")).json()
    assert (me["subject"], me["company_id"], me["role"]) == ("GOM-2001", "gomti", "employee")


def test_role_comes_from_the_directory_not_the_request(client):
    response = client.post(
        "/v1/auth/login", json={"company_id": "yamuna", "employee_id": "YAM-1001", "role": "policy_admin"}
    )
    assert response.status_code == 422
    assert login(client, "yamuna", "YAM-1001").json()["employee"]["role"] == "employee"


def test_a_wrong_company_code_and_a_wrong_employee_id_get_the_same_reply(client):
    """The sign-in form must not confirm which companies are clients."""
    no_company = login(client, "nosuchco", "YAM-1001")
    wrong_company = login(client, "gomti", "YAM-1001")
    no_employee = login(client, "gomti", "GOM-9999")
    replies = [r.json() for r in (no_company, wrong_company, no_employee)]
    assert {r.status_code for r in (no_company, wrong_company, no_employee)} == {401}
    assert {r["status"] for r in replies} == {"unknown_account"}
    assert len({r["message"] for r in replies}) == 1
    assert "Gomti" not in no_employee.text and "YAM-1001" not in wrong_company.text


def test_the_client_list_is_not_published(client):
    assert client.get("/v1/auth/companies").status_code in {404, 405}


def test_inactive_employee(client):
    response = login(client, "gomti", "GOM-2003")
    assert response.status_code == 403
    assert response.json()["status"] == "account_inactive"


def test_logout_revokes_the_token(client):
    headers = {"Authorization": f"Bearer {token(client)}"}
    response = client.post("/v1/auth/logout", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "signed_out"
    assert client.get("/v1/me", headers=headers).status_code == 401
    assert client.post("/v1/auth/logout", headers=headers).status_code == 401


def test_tokens_from_two_logins_both_work(client):
    first, second = auth(client), auth(client)
    assert first != second
    assert client.get("/v1/me", headers=first).status_code == 200
    assert client.get("/v1/me", headers=second).status_code == 200


def test_session_survives_a_server_restart(settings):
    with TestClient(create_app(settings)) as before:
        headers = auth(before)
    with TestClient(create_app(settings)) as after:
        assert after.get("/v1/me", headers=headers).status_code == 200


def test_login_cookie_does_not_authenticate_api_calls(client):
    login(client)
    assert "cpa_session" in client.cookies
    assert client.get("/v1/me").status_code == 401


def test_sample_accounts_listed_only_in_dev(client, settings):
    accounts = client.get("/dev/accounts").json()
    assert {
        "company_id": "gomti",
        "company_name": "Gomti Logistics",
        "employee_id": "GOM-2003",
        "name": "Arjun Yadav",
        "role": "employee",
        "active": False,
    } in accounts
    with TestClient(create_app(settings.model_copy(update={"app_env": Environment.PROD}))) as prod:
        assert prod.get("/dev/accounts").status_code == 404
        assert prod.post("/dev/token", json={"subject": "x", "company_id": "a"}).status_code == 404


def test_store_outage_fails_closed_with_503(client):
    headers = auth(client)
    container = client.app.state.container
    with container.token_store._transaction() as conn:
        conn.execute("DROP TABLE issued_tokens")
    response = client.get("/v1/me", headers=headers)
    assert response.status_code == 503
    assert response.json()["status"] == "auth_unavailable"
    assert response.headers["Retry-After"] == "30"


def test_directory_edit_takes_effect_without_a_restart(settings, tmp_path):
    directory = tmp_path / "employees.json"
    raw = json.loads(settings.employee_directory.read_text())
    directory.write_text(json.dumps(raw))
    with TestClient(create_app(settings.model_copy(update={"employee_directory": directory}))) as client:
        headers = {"Authorization": f"Bearer {login(client, 'yamuna', 'YAM-1002').json()['access_token']}"}
        assert client.get("/v1/me", headers=headers).status_code == 200

        # HR marks the employee as having left while they are signed in.
        raw["companies"][0]["employees"][1]["active"] = False
        directory.write_text(json.dumps(raw))
        os.utime(directory, ns=(0, directory.stat().st_mtime_ns + 10**9))

        assert client.get("/v1/me", headers=headers).status_code == 401
        assert login(client, "yamuna", "YAM-1002").json()["status"] == "account_inactive"
