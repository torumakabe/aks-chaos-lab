from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
import scripts.configure_sre_agent as bootstrap


def test_bootstrap_is_optional_and_does_not_configure_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_SRE_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED", raising=False)
    monkeypatch.setattr(
        bootstrap.incidents, "agent_connection", lambda: pytest.fail("Azure called")
    )
    assert bootstrap.main([]) == 0
    assert bootstrap.main(["github"]) == 1
    monkeypatch.setenv("AZURE_SRE_AGENT_ENABLED", "true")
    monkeypatch.setattr(
        bootstrap.incidents, "agent_connection", lambda: ("https://agent", "test", {})
    )
    monkeypatch.setattr(
        bootstrap, "configure_github", lambda *_: pytest.fail("GitHub configured")
    )
    knowledge: list[str] = []
    monkeypatch.setattr(
        bootstrap, "configure_knowledge", lambda *_: knowledge.append("readme")
    )
    assert bootstrap.main([]) == 0
    assert knowledge == ["readme"]


@pytest.mark.parametrize(
    "uri",
    [
        "https://vault.vault.azure.net/secrets/key",
        "http://vault.vault.azure.net/keys/key",
        "https://vault.vault.azure.net.evil.example/keys/key",
    ],
)
def test_github_rejects_non_key_uris(uri: str) -> None:
    with pytest.raises(bootstrap.incidents.ConfigurationError):
        bootstrap.github_payload("Iv123", uri)


@pytest.mark.parametrize("state", ["Ready", "wrong-branch", "Failed"])
def test_github_configures_and_verifies_repository(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    monkeypatch.setenv("AZURE_SRE_AGENT_ENABLED", "true")
    monkeypatch.setenv("AZURE_SRE_AGENT_VAULT_URI", "https://vault.vault.azure.net/")
    monkeypatch.setattr(bootstrap.sys, "stdin", StringIO("Iv123\n"))
    monkeypatch.setattr(
        bootstrap.incidents,
        "agent_connection",
        lambda **_: ("https://agent", "test", {}),
    )
    monkeypatch.setattr(
        bootstrap, "verify_github_audit", lambda *_: pytest.fail("Audit waited")
    )
    monkeypatch.setattr(
        bootstrap, "configure_knowledge", lambda *_: pytest.fail("Bootstrap called")
    )
    writes: list[tuple[str, Any]] = []

    def request(endpoint: str, token: str, path: str, **kwargs: Any) -> Any:
        if kwargs.get("method") == "PUT":
            writes.append((path, kwargs["body"]))
            return {"success": True}
        if path == "/api/v2/github/domains":
            return {"values": [{"name": "github.com", **writes[0][1]}]}
        return {
            "properties": {
                "url": "https://github.com/torumakabe/aks-chaos-lab",
                "branch": "other" if state == "wrong-branch" else "main",
                "type": "GitHub",
                "cloneStatus": "Failed" if state == "Failed" else "Ready",
            }
        }

    monkeypatch.setattr(bootstrap, "request", request)
    assert bootstrap.main(["github"]) == (0 if state == "Ready" else 1)
    assert writes[0][1] == {
        "authType": "GitHubApp",
        "clientId": "Iv123",
        "privateKeySecretUri": "https://vault.vault.azure.net/keys/sre-agent-github-app-key",
    }
    assert len(writes) == 2


def test_github_retries_share_one_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    deadlines: list[float] = []
    monkeypatch.setenv("AZURE_SRE_AGENT_ENABLED", "true")
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        bootstrap.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    def connect(*, deadline: float) -> tuple[str, str, dict[str, Any]]:
        deadlines.append(deadline)
        clock[0] += 75
        return "https://agent", "test", {}

    def request(url: str, token: str, **kwargs: Any) -> Any:
        deadline = kwargs["deadline"]
        deadlines.append(deadline)
        clock[0] += bootstrap.incidents.remaining_timeout(deadline, 30)
        if url.endswith(bootstrap.DOMAIN_PATH):
            return {"success": True}
        if url.endswith("/api/v2/github/domains"):
            return {"values": [{"name": "github.com", **body}]}
        if kwargs.get("method") == "PUT":
            return {}
        raise bootstrap.incidents.ApiRequestError(503, "not ready")

    body = bootstrap.github_payload("Iv123", "https://vault.vault.azure.net/keys/key")
    monkeypatch.setattr(bootstrap.incidents, "agent_connection", connect)
    monkeypatch.setattr(bootstrap.incidents, "_request_json", request)
    assert (
        bootstrap.main(
            ["github", "--client-id", "Iv123", "--key-uri", body["privateKeySecretUri"]]
        )
        == 1
    )
    assert clock[0] == 300
    assert len(deadlines) > 4
    assert set(deadlines) == {300}


def test_github_cancel_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SRE_AGENT_ENABLED", "true")

    def interrupted(**kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap.incidents, "agent_connection", interrupted)
    assert (
        bootstrap.main(
            [
                "github",
                "--client-id",
                "Iv123",
                "--key-uri",
                "https://vault.vault.azure.net/keys/key",
            ]
        )
        == 130
    )


@pytest.mark.parametrize("indexed", [True, False])
def test_knowledge_waits_for_indexing_and_stops_at_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, indexed: bool
) -> None:
    content = b"# Project\n"
    (tmp_path / "README.md").write_bytes(content)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    clock = [0.0]
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        bootstrap.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    def request(endpoint: str, token: str, path: str, **kwargs: Any) -> Any:
        if "upload" in path:
            assert content in kwargs["raw_body"]
            return {"uploaded": [bootstrap.KNOWLEDGE_NAME], "indexingTriggered": True}
        return {
            "files": [
                {
                    "name": bootstrap.KNOWLEDGE_NAME,
                    "isIndexed": indexed and clock[0] >= 5,
                    "errorReason": "File could not be indexed."
                    if clock[0] == 0
                    else None,
                }
            ]
        }

    monkeypatch.setattr(bootstrap, "request", request)
    if indexed:
        bootstrap.configure_knowledge("https://agent", "test")
        assert clock[0] == 5
    else:
        with pytest.raises(bootstrap.incidents.ConfigurationError):
            bootstrap.configure_knowledge("https://agent", "test")
        assert clock[0] == 300


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("Identity", {"claim": {"oid": "principal"}}),
        ("Identity", {"claim": {"oid": "old-agent"}}),
        ("CallerIpAddress", "203.0.113.1"),
        ("RequestUri", "https://vault.vault.azure.net/keys/other/version/sign"),
        (
            "RequestUri",
            "https://other.vault.azure.net/keys/sre-agent-github-app-key/version/sign",
        ),
    ],
)
def test_audit_accepts_only_current_identity_private_ip_and_key(
    monkeypatch: pytest.MonkeyPatch, change: str, value: Any
) -> None:
    for name, setting in {
        "AZURE_SUBSCRIPTION_ID": "subscription",
        "AZURE_SRE_AGENT_ID": "/agents/current",
        "AZURE_SRE_AGENT_VAULT_ID": "/vaults/current",
        "AZURE_SRE_AGENT_VAULT_URI": "https://vault.vault.azure.net/",
        "AZURE_LOG_ANALYTICS_WORKSPACE_ID": "workspace",
    }.items():
        monkeypatch.setenv(name, setting)
    row = {
        "Identity": {"claim": {"oid": "principal"}},
        "CallerIpAddress": "10.20.0.4",
        "RequestUri": "https://vault.vault.azure.net/keys/sre-agent-github-app-key/version/sign",
        change: value,
    }
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> str:
        calls.append(arguments)
        if arguments[:2] == ["resource", "show"]:
            if "/agents/current" in arguments:
                return json.dumps(
                    {
                        "identity": {"principalId": "principal"},
                        "properties": {
                            "vnetConfiguration": {
                                "subnetResourceId": "/subnets/current"
                            }
                        },
                    }
                )
            assert "/subnets/current" in arguments
            return json.dumps({"properties": {"addressPrefix": "10.20.0.0/27"}})
        assert arguments[:3] == ["monitor", "log-analytics", "query"]
        assert "/vaults/current" in arguments[arguments.index("--analytics-query") + 1]
        return json.dumps([row])

    monkeypatch.setattr(bootstrap.incidents, "_run_az", run)
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if value == {"claim": {"oid": "principal"}}:
        bootstrap.verify_github_audit(since)
    else:
        with pytest.raises(bootstrap.incidents.ConfigurationError):
            bootstrap.verify_github_audit(since)
    assert len(calls) == 3
