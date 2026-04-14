"""Test to verify telemetry is disabled in test environment."""

import os

from app.config import Settings


def test_telemetry_disabled_in_tests() -> None:
    """Verify that telemetry is disabled in test environment."""
    # Check environment variables
    assert os.getenv("TELEMETRY_ENABLED") == "false"

    # Check settings
    settings = Settings()
    assert not settings.telemetry_enabled
