"""Small helpers shared by API tests."""


def token(client, company="yamuna", role="employee", subject="emp-7") -> str:
    response = client.post("/dev/token", json={"subject": subject, "company_id": company, "role": role})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(client, company="yamuna", role="employee") -> dict[str, str]:
    return {"Authorization": f"Bearer {token(client, company, role)}"}
