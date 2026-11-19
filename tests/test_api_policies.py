import json
import shutil

from fastapi.testclient import TestClient

from app.container import Container
from app.llm.fake import ScriptedLLM
from app.main import create_app
from tests.conftest import DATA_DIR, MARKDOWN_DIR, make_settings
from tests.helpers import auth, login

YAMUNA_V2 = (MARKDOWN_DIR / "yamuna" / "v2.md").read_text()


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
    old = (MARKDOWN_DIR / "yamuna" / "v1.md").read_text()
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


def cites(*labels, answer="The escort starts at the time in the policy."):
    return json.dumps({"verdict": "answered", "answer": answer, "citations": list(labels)})


def bearer(response):
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_after_an_upload_only_the_new_version_answers(settings):
    """The brief's "after a policy update, only the new version answers", through the API."""
    settings = settings.model_copy(update={"evidence_min_similarity": 0.0})
    llm = ScriptedLLM([cites("S1"), cites("S1")])
    v3 = YAMUNA_V2.replace("version: 2", "version: 3").replace("7:30 PM", "7:00 PM")
    question = {"question": "What time does the security escort start on night drops?"}

    with TestClient(create_app(settings, Container(settings, llm=llm))) as client:
        before = client.post("/v1/ask", json=question, headers=auth(client)).json()
        upload = client.post("/v1/policies", json={"content": v3}, headers=auth(client, role="policy_admin"))
        after = client.post("/v1/ask", json=question, headers=auth(client)).json()

    assert upload.json()["activated"] and upload.json()["previous_version"] == 2
    assert before["policy"]["version"] == 2 and "7:30 PM" in llm.calls[0].user
    assert after["policy"]["version"] == 3
    assert after["citations"] and {c["version"] for c in after["citations"]} == {3}
    assert "7:00 PM" in llm.calls[1].user and "7:30 PM" not in llm.calls[1].user


KAVERI_V1 = """---
company_id: kaveri
company_name: Kaveri Textiles
doc_id: kaveri-transport-policy
title: Kaveri Textiles Transport Policy
version: 1
effective_from: 2026-12-01
---

# Kaveri Textiles Transport Policy

## 1. Weekend pickups

Cabs are not provided on Saturdays or Sundays.
"""

KAVERI_DIRECTORY_ENTRY = {
    "company_id": "kaveri",
    "company_name": "Kaveri Textiles",
    "employees": [
        {"employee_id": "KAV-4001", "name": "Asha Pillai", "role": "employee"},
        {"employee_id": "KAV-4002", "name": "Ravi Menon", "role": "policy_admin"},
    ],
}


def test_a_new_client_is_onboarded_with_a_directory_entry_and_a_first_upload(tmp_path):
    """The README's onboarding steps: no registration endpoint, no restart."""
    directory = tmp_path / "employees.json"
    shutil.copy(DATA_DIR / "employees.json", directory)
    settings = make_settings(tmp_path / "data").model_copy(
        update={"employee_directory": directory, "evidence_min_similarity": 0.0}
    )
    llm = ScriptedLLM([cites("S1", answer="No, cabs are not provided at weekends.")])

    with TestClient(create_app(settings, Container(settings, llm=llm))) as client:
        assert login(client, "kaveri", "KAV-4002").status_code == 401

        entries = json.loads(directory.read_text())
        entries["companies"].append(KAVERI_DIRECTORY_ENTRY)
        directory.write_text(json.dumps(entries))

        admin = bearer(login(client, "kaveri", "KAV-4002"))
        upload = client.post("/v1/policies", json={"content": KAVERI_V1}, headers=admin)
        employee = bearer(login(client, "kaveri", "KAV-4001"))
        answer = client.post("/v1/ask", json={"question": "Can I book a cab on Saturday?"}, headers=employee)
        from_yamuna = client.post(
            "/v1/ask", json={"question": "What is the weekend rule at Kaveri Textiles?"}, headers=auth(client)
        )

    assert upload.json()["activated"] and upload.json()["previous_version"] is None
    assert answer.json()["status"] == "answered"
    assert {c["document"] for c in answer.json()["citations"]} == {"Kaveri Textiles Transport Policy"}
    assert "Yamuna" not in llm.calls[0].user and "Gomti" not in llm.calls[0].user
    # Existing clients now treat the new company as "another company".
    assert from_yamuna.json()["status"] == "other_company"
    assert len(llm.calls) == 1
