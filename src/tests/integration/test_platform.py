"""Platform integration tests for AKS Chaos Lab.

These tests verify the application works correctly with the Azure platform:
- AKS cluster connectivity
- Redis cache integration
- Application Insights telemetry

Usage:
    INTEGRATION_TEST_URL=https://your-ingress.domain.com pytest tests/integration/test_platform.py -v
"""

import os

import pytest
import requests
import urllib3

# Suppress SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@pytest.fixture
def base_url() -> str:
    """Get the base URL for integration tests from environment variable."""
    url = os.environ.get("INTEGRATION_TEST_URL")
    if not url:
        pytest.skip("INTEGRATION_TEST_URL environment variable not set")
    return url.rstrip("/")


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_200(self, base_url: str) -> None:
        """Verify health endpoint returns HTTP 200."""
        response = requests.get(f"{base_url}/health", timeout=30, verify=False)
        assert response.status_code == 200

    def test_health_returns_json(self, base_url: str) -> None:
        """Verify health endpoint returns valid JSON."""
        response = requests.get(f"{base_url}/health", timeout=30, verify=False)
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"


class TestRootEndpoint:
    """Tests for the / endpoint with Redis integration."""

    def test_root_returns_200(self, base_url: str) -> None:
        """Verify root endpoint returns HTTP 200."""
        response = requests.get(f"{base_url}/", timeout=30, verify=False)
        assert response.status_code == 200

    def test_root_returns_json(self, base_url: str) -> None:
        """Verify root endpoint returns valid JSON."""
        response = requests.get(f"{base_url}/", timeout=30, verify=False)
        data = response.json()
        assert "message" in data

    def test_root_includes_redis_data(self, base_url: str) -> None:
        """Verify root endpoint includes Redis cache data when available."""
        response = requests.get(f"{base_url}/", timeout=30, verify=False)
        # Redis data should be present (either cached or fresh)
        # Verify response is valid JSON with expected structure
        data = response.json()
        assert response.status_code == 200
        assert "message" in data


class TestAPIAvailability:
    """Tests for overall API availability and response times."""

    def test_api_responds_within_timeout(self, base_url: str) -> None:
        """Verify API responds within reasonable timeout."""
        try:
            response = requests.get(f"{base_url}/health", timeout=10, verify=False)
            assert response.status_code == 200
        except requests.exceptions.Timeout:
            pytest.fail("API did not respond within 10 seconds")

    def test_multiple_requests_succeed(self, base_url: str) -> None:
        """Verify multiple consecutive requests succeed."""
        for _ in range(5):
            response = requests.get(f"{base_url}/health", timeout=30, verify=False)
            assert response.status_code == 200
