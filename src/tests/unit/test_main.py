from fastapi.testclient import TestClient


def test_root_success(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["message"].startswith("Hello")
    assert "timestamp" in body


def test_health_schema(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code in (200, 503)
    body = r.json()
    assert body["status"] in ("healthy", "unhealthy")
    assert "redis" in body
    assert isinstance(body["redis"].get("connected"), bool)
    assert isinstance(body["redis"].get("latency_ms"), int)
