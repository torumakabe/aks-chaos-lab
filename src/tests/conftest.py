import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.main import settings as app_settings


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    # Set environment variables to disable telemetry completely in tests
    os.environ["TELEMETRY_ENABLED"] = "false"
    os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = ""

    # Disable Redis and Telemetry for deterministic tests
    app_settings.redis_enabled = False  # type: ignore[assignment]
    app_settings.redis_host = None  # type: ignore[assignment]
    app_settings.telemetry_enabled = False  # type: ignore[assignment]
    app_settings.applicationinsights_connection_string = None  # type: ignore[assignment]
    app_settings.appinsights_connection_string = None  # type: ignore[assignment]

    with TestClient(app) as c:
        yield c
