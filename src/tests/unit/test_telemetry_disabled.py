"""Test to verify telemetry is disabled in test environment."""

import os

from app.config import Settings


def test_telemetry_disabled_in_tests() -> None:
    """Verify that telemetry is disabled in test environment."""
    # Check environment variables
    assert os.getenv("TELEMETRY_ENABLED") == "false"
    assert os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") == ""

    # Check settings
    settings = Settings()
    assert not settings.telemetry_enabled
    assert (
        settings.applicationinsights_connection_string is None
        or settings.applicationinsights_connection_string == ""
    )
