from __future__ import annotations

import subprocess
import sys
import time
from io import BytesIO
from typing import Any

import pytest
import scripts.configure_sre_agent_incidents as incidents


def test_azure_cli_timeout_terminates_without_exposing_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(incidents.shutil, "which", lambda _: sys.executable)
    start = time.monotonic()
    with pytest.raises(incidents.ConfigurationError) as error:
        incidents._run_az(
            ["-c", "import time; print('dummy-token', flush=True); time.sleep(10)"],
            secret_output=True,
            deadline=start + 0.2,
        )
    assert time.monotonic() - start < 3
    assert "dummy-token" not in str(error.value)
    assert isinstance(error.value.__cause__, subprocess.TimeoutExpired)


@pytest.mark.parametrize("timeout", [False, True])
def test_api_request_preserves_auth_body_and_timeout_budget(
    monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    monkeypatch.setattr(incidents.time, "monotonic", lambda: 100)

    def urlopen(request: Any, **kwargs: Any) -> BytesIO:
        assert request.get_header("Authorization") == "Bearer dummy-token"
        assert (
            request.get_header("Content-type") == "multipart/form-data; boundary=test"
        )
        assert request.data == b"file bytes"
        assert kwargs["timeout"] == 7
        if timeout:
            raise TimeoutError
        return BytesIO(b"{}")

    monkeypatch.setattr(incidents.urllib.request, "urlopen", urlopen)

    def request() -> Any:
        return incidents._request_json(
            "https://agent/upload",
            "dummy-token",
            method="POST",
            raw_body=b"file bytes",
            content_type="multipart/form-data; boundary=test",
            deadline=107,
        )

    if timeout:
        with pytest.raises(incidents.TransientApiError):
            request()
    else:
        assert request() == {}


@pytest.mark.parametrize("autonomous", [False, True])
def test_incident_configuration_is_opt_in_and_requires_review(
    monkeypatch: pytest.MonkeyPatch, autonomous: bool
) -> None:
    monkeypatch.delenv("AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED", raising=False)
    monkeypatch.setattr(
        incidents, "agent_connection", lambda: pytest.fail("Azure called")
    )
    assert incidents.main() == 0
    monkeypatch.setenv("AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED", "true")
    monkeypatch.setattr(
        incidents,
        "agent_connection",
        lambda: (
            "https://agent",
            "dummy",
            {"incidentManagementConfiguration": {"type": "AzMonitor"}},
        ),
    )
    stored: dict[str, Any] = {
        "unrelated-plan": {
            "name": "unrelated-plan",
            "properties": {
                "agentMode": "autonomous" if autonomous else "review",
                "isEnabled": True,
            },
        },
    }
    unrelated = stored["unrelated-plan"].copy()
    writes: list[dict[str, Any]] = []

    def request(url: str, token: str, **kwargs: Any) -> Any:
        if url.endswith(incidents.PLATFORM_STATUS_PATH):
            return {"incidentPlatformType": "AzMonitor"}
        if kwargs.get("method") == "PUT":
            body = kwargs["body"]
            writes.append(body)
            stored[body["name"]] = body
            return {}
        return {"value": list(stored.values())}

    monkeypatch.setattr(incidents, "_request_json", request)
    assert incidents.main() == (1 if autonomous else 0)
    assert stored["unrelated-plan"] == unrelated
    if autonomous:
        assert not writes
    else:
        assert len(writes) == 1
        properties = writes[0]["properties"]
        assert properties["agentMode"] == "review"
        assert properties["incidentPlatform"] == "AzMonitor"
        assert properties["priorities"] == ["Sev3"]
        assert properties["titleContains"] == "SLI short-window alert"
