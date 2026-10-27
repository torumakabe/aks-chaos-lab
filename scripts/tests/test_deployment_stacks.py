from __future__ import annotations

import json
import subprocess

import pytest
from scripts import deployment_stacks


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_alpha_environment_overrides_config(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.delenv("AZD_ALPHA_ENABLE_DEPLOYMENT.STACKS", raising=False)
    monkeypatch.setenv("AZD_ALPHA_ENABLE_DEPLOYMENT_STACKS", value)
    monkeypatch.setattr(deployment_stacks.shutil, "which", lambda _: None)
    assert deployment_stacks.enabled() is expected


@pytest.mark.parametrize(
    "alpha,expected",
    [({}, False), ({"deployment": {"stacks": "on"}}, True), ({"all": "on"}, True)],
)
def test_alpha_config(
    monkeypatch: pytest.MonkeyPatch, alpha: dict[str, object], expected: bool
) -> None:
    for name in (
        "AZD_ALPHA_ENABLE_DEPLOYMENT.STACKS",
        "AZD_ALPHA_ENABLE_DEPLOYMENT_STACKS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(deployment_stacks.shutil, "which", lambda _: "azd")
    monkeypatch.setattr(
        deployment_stacks.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps({"alpha": alpha}), ""
        ),
    )
    assert deployment_stacks.enabled() is expected


def test_disabled_guard_fails_without_changing_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(deployment_stacks, "enabled", lambda: False)
    assert deployment_stacks.main() == 1
    assert "azd config set alpha.deployment.stacks on" in capsys.readouterr().err
