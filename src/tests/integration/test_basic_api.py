from fastapi.testclient import TestClient

from app.main import app
from app.main import settings as app_settings


def test_get_root_basic() -> None:
    app_settings.redis_enabled = False  # type: ignore[assignment]
    app_settings.redis_host = None  # type: ignore[assignment]
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
