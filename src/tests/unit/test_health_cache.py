import time

from fastapi.testclient import TestClient

from app.main import _health_cache, app
from app.models import HealthResponse


def test_health_uses_cached_unhealthy_response() -> None:
    # Prepare cached unhealthy response with fresh timestamp
    payload = HealthResponse(
        status="unhealthy",
        redis={"connected": False, "latency_ms": 0},
        timestamp="2025-08-11T00:00:00Z",
    )
    _health_cache.clear()
    _health_cache["payload"] = payload
    _health_cache["status_code"] = 503
    _health_cache["_ts"] = time.monotonic()

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 503
        assert r.json() == payload.model_dump()

