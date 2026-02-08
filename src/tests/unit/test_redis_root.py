"""Tests for root endpoint with Redis enabled via DI overrides."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, get_redis_client, get_settings


def _redis_enabled_settings() -> Settings:
    """Return Settings with Redis enabled for testing."""
    s = Settings()
    s.redis_enabled = True
    s.redis_host = "test-host"
    s.telemetry_enabled = False
    s.applicationinsights_connection_string = None
    s.appinsights_connection_string = None
    return s


class TestRootWithRedisEnabled:
    """Redis-enabled root endpoint tests using DI overrides."""

    def test_root_redis_success(self) -> None:
        """Redis enabled + normal operation returns 200."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value="cached-data")
        mock_client.increment = AsyncMock(return_value=1)

        s = _redis_enabled_settings()
        app.dependency_overrides[get_settings] = lambda: s
        app.dependency_overrides[get_redis_client] = (
            lambda: mock_client
        )
        try:
            with TestClient(app) as c:
                r = c.get("/")
                assert r.status_code == 200
                assert r.json()["redis_data"] == "cached-data"
        finally:
            app.dependency_overrides.clear()

    def test_root_redis_failure_returns_503(self) -> None:
        """Redis enabled + exception returns 503."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=ConnectionError("connection refused")
        )

        s = _redis_enabled_settings()
        app.dependency_overrides[get_settings] = lambda: s
        app.dependency_overrides[get_redis_client] = (
            lambda: mock_client
        )
        try:
            with TestClient(app) as c:
                r = c.get("/")
                assert r.status_code == 503
                body = r.json()
                assert body["error"] == "Service Unavailable"
                assert "Redis" in body["detail"]
        finally:
            app.dependency_overrides.clear()
