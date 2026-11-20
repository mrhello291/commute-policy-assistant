import json
import shutil

from fastapi.testclient import TestClient

from app.container import Container
from app.llm.fake import ScriptedLLM
from app.main import create_app
from scripts.make_policy_pdfs import render_policy_pdf
from tests.conftest import DATA_DIR, MARKDOWN_DIR, POLICY_DIR, make_settings
from tests.helpers import auth, login

YAMUNA_V2 = (MARKDOWN_DIR / "yamuna" / "v2.md").read_text()


def yamuna_v3() -> bytes:
    """Yamuna's v2 as HR would send v3: a PDF with a new version number and one changed rule."""
    return render_policy_pdf(YAMUNA_V2.replace("version: 2", "version: 3").replace("INR 900", "INR 950"))


def upload(client, data: bytes | str, headers, name: str = "policy.pdf"):
    return client.post("/v1/policies", files={"file": (name, data, "application/pdf")}, headers=headers)


def test_employee_sees_their_own_live_policy(client):
    body = client.get("/v1/policies/active", headers=auth(client, "gomti")).json()
    assert body == {
        "company_id": "gomti",
        "company_name": "Gomti Logistics",
        "title": "Gomti Logistics Commute Policy",
        "version": 1,
    }


def test_admin_uploads_new_version(client):
    response = upload(client, yamuna_v3(), auth(client, role="policy_admin"))
    assert response.status_code == 200
    assert response.json()["activated"] and response.json()["previous_version"] == 2
    live = client.get("/v1/policies/active", headers=auth(client)).json()
    assert live["version"] == 3


def test_employee_cannot_upload(client):
    response = upload(client, yamuna_v3(), auth(client))
    assert response.status_code == 403


def test_admin_cannot_upload_for_another_company(client):
    headers = auth(client, "gomti", role="policy_admin")
    response = upload(client, yamuna_v3(), headers)
    assert response.status_code == 403
    assert client.get("/v1/policies/active", headers=auth(client)).json()["version"] == 2


def test_old_version_upload_is_a_conflict(client):
    old = (POLICY_DIR / "yamuna" / "v1.pdf").read_bytes()
    response = upload(client, old, auth(client, role="policy_admin"))
    assert response.status_code == 409
    assert "Nothing was changed" in response.json()["message"]


def test_a_file_that_is_not_a_pdf_is_refused(client):
    response = upload(client, "just some text", auth(client, role="policy_admin"), name="policy.txt")
    assert response.status_code == 422
    assert "not a PDF" in response.json()["message"]


def test_a_damaged_pdf_is_refused_and_the_live_version_kept(client):
    truncated = yamuna_v3()[:3000]
    response = upload(client, truncated, auth(client, role="policy_admin"))
    assert response.status_code == 422
    assert "Nothing was changed" in response.json()["message"]
    assert client.get("/v1/policies/active", headers=auth(client)).json()["version"] == 2


def without_company(version: int) -> bytes:
    """A policy PDF as most companies write one: no company or company ID in it."""
    from tests.test_pdf_parser import Pdf

    pdf = Pdf().title("Employee Transport Policy")
    for text in ("Document ID: yamuna-transport-policy", f"Version: {version}", "Effective from: 1 November 2026"):
        pdf.line(text)
    return pdf.heading("1. Scope").para("Every employee on a rostered shift.").bytes()


def test_a_pdf_that_names_no_company_is_filed_under_the_uploaders(client):
    response = upload(client, without_company(3), auth(client, role="policy_admin"))

    assert response.status_code == 200 and response.json()["company_id"] == "yamuna"
    live = client.get("/v1/policies/active", headers=auth(client)).json()
    assert (live["version"], live["company_name"]) == (3, "Yamuna Software Services")
    # The other companies are untouched.
    assert client.get("/v1/policies/active", headers=auth(client, "gomti")).json()["version"] == 1


def test_a_future_dated_upload_says_when_to_send_it(client):
    text = YAMUNA_V2.replace("version: 2", "version: 3")
    v3 = render_policy_pdf(text.replace("effective_from: 2026-10-01", "effective_from: 2099-01-15"))
    response = upload(client, v3, auth(client, role="policy_admin"))
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "not_yet_effective"
    assert "15 January 2099" in body["message"] and "version 2 stays in use" in body["message"]
    assert client.get("/v1/policies/active", headers=auth(client)).json()["version"] == 2


def test_a_second_document_does_not_replace_the_policy(client):
    """A one-section SOP with its own Document ID used to replace Gomti's whole 22-section policy."""
    from tests.test_pdf_parser import Pdf

    pdf = Pdf().title("Night Shift Escort SOP")
    for text in ("Document ID: night-escort-sop", "Version: 2", "Effective from: 1 November 2026"):
        pdf.line(text)
    sop = pdf.heading("1. Escort").para("A guard rides on every drop after 8 PM.").bytes()

    response = upload(client, sop, auth(client, "gomti", role="policy_admin"))
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "different_document"
    assert '"Gomti Logistics Commute Policy" (Document ID gomti-commute-policy)' in body["message"]
    live = client.get("/v1/policies/active", headers=auth(client, "gomti")).json()
    assert (live["title"], live["version"]) == ("Gomti Logistics Commute Policy", 1)


def test_markdown_is_still_accepted(client):
    v3 = YAMUNA_V2.replace("version: 2", "version: 3")
    response = upload(client, v3, auth(client, role="policy_admin"), name="v3.md")
    assert response.status_code == 200 and response.json()["version"] == 3


def test_upload_without_a_file_is_a_validation_error(client):
    response = client.post("/v1/policies", headers=auth(client, role="policy_admin"))
    assert response.status_code == 422


def test_failed_ingest_reports_that_old_version_is_kept(client, monkeypatch):
    container = client.app.state.container

    def broken(_texts):
        raise ConnectionError("embedder down")

    monkeypatch.setattr(container.embedder, "embed", broken)
    response = upload(client, yamuna_v3(), auth(client, role="policy_admin"))
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
    v3 = render_policy_pdf(YAMUNA_V2.replace("version: 2", "version: 3").replace("7:30 PM", "7:00 PM"))
    question = {"question": "What time does the security escort start on night drops?"}

    with TestClient(create_app(settings, Container(settings, llm=llm))) as client:
        before = client.post("/v1/ask", json=question, headers=auth(client)).json()
        uploaded = upload(client, v3, auth(client, role="policy_admin"))
        after = client.post("/v1/ask", json=question, headers=auth(client)).json()

    assert uploaded.json()["activated"] and uploaded.json()["previous_version"] == 2
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
effective_from: 2026-11-01
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
        uploaded = upload(client, render_policy_pdf(KAVERI_V1), admin)
        employee = bearer(login(client, "kaveri", "KAV-4001"))
        answer = client.post("/v1/ask", json={"question": "Can I book a cab on Saturday?"}, headers=employee)
        from_yamuna = client.post(
            "/v1/ask", json={"question": "What is the weekend rule at Kaveri Textiles?"}, headers=auth(client)
        )

    assert uploaded.json()["activated"] and uploaded.json()["previous_version"] is None
    assert answer.json()["status"] == "answered"
    assert {c["document"] for c in answer.json()["citations"]} == {"Kaveri Textiles Transport Policy"}
    assert "Yamuna" not in llm.calls[0].user and "Gomti" not in llm.calls[0].user
    # Existing clients now treat the new company as "another company".
    assert from_yamuna.json()["status"] == "other_company"
    assert len(llm.calls) == 1
