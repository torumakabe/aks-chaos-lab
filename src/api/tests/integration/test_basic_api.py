from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, get_redis_client, get_settings


def test_get_root_basic() -> None:
    """Verify root endpoint returns 200 with Redis disabled."""
    s = Settings()
    s.redis_enabled = False
    s.redis_host = None
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_redis_client] = lambda: None
    try:
        with TestClient(app) as client:
            r = client.get("/")
            assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()
