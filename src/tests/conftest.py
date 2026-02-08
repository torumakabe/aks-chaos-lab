from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, get_redis_client, get_settings


def _test_settings() -> Settings:
    """Return Settings with Redis and telemetry disabled for tests."""
    s = Settings()
    s.redis_enabled = False
    s.redis_host = None
    s.telemetry_enabled = False
    s.applicationinsights_connection_string = None
    s.appinsights_connection_string = None
    return s


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    """Provide a TestClient with Redis/telemetry disabled via DI overrides."""
    _cached = _test_settings()
    app.dependency_overrides[get_settings] = lambda: _cached
    app.dependency_overrides[get_redis_client] = lambda: None

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
