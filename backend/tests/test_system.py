from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_responds_without_db():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"ok", "degraded"}


def test_metrics_prometheus_format():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "app_uptime_seconds" in resp.text


def test_role_required():
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"X-Role": "hacker"}).status_code == 401
    resp = client.get("/me", headers={"X-Role": "seller"})
    assert resp.status_code == 200
    assert resp.json() == {"role": "seller"}
