"""Small helpers shared by API tests."""

# One sample account per company and role, from data/employees.json.
SAMPLE_ACCOUNTS = {
    ("yamuna", "employee"): "YAM-1001",
    ("yamuna", "policy_admin"): "YAM-1004",
    ("gomti", "employee"): "GOM-2001",
    ("gomti", "policy_admin"): "GOM-2004",
    ("medhavi", "employee"): "MED-3001",
    ("medhavi", "policy_admin"): "MED-3003",
}


def login(client, company="yamuna", employee_id="YAM-1001"):
    return client.post("/v1/auth/login", json={"company_id": company, "employee_id": employee_id})


def token(client, company="yamuna", role="employee") -> str:
    response = login(client, company, SAMPLE_ACCOUNTS[(company, role)])
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(client, company="yamuna", role="employee") -> dict[str, str]:
    return {"Authorization": f"Bearer {token(client, company, role)}"}
