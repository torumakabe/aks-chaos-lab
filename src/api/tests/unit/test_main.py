from fastapi.testclient import TestClient

from app.main import _health_cache


def test_root_success(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["message"].startswith("Hello")
    assert "timestamp" in body


def test_livez_schema(client: TestClient) -> None:
    r = client.get("/livez")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "alive"
    assert "timestamp" in body
    assert "redis" not in body


def test_health_schema(client: TestClient) -> None:
    _health_cache.clear()
    r = client.get("/health")
    assert r.status_code in (200, 503)
    body = r.json()
    assert body["status"] in ("healthy", "unhealthy")
    assert "redis" in body
    assert isinstance(body["redis"].get("connected"), bool)
    assert isinstance(body["redis"].get("latency_ms"), int)


def test_readyz_schema(client: TestClient) -> None:
    _health_cache.clear()
    r = client.get("/readyz")
    assert r.status_code in (200, 503)
    body = r.json()
    assert body["status"] in ("healthy", "unhealthy")
    assert "redis" in body
    assert isinstance(body["redis"].get("connected"), bool)
    assert isinstance(body["redis"].get("latency_ms"), int)
