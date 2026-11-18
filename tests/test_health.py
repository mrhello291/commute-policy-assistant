def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_demo_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Commute Policy Assistant" in response.text
