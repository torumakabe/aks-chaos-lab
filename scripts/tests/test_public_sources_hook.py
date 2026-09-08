from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-public-sources.py"
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("public_sources_hook", SCRIPT)
assert spec is not None and spec.loader is not None
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


@pytest.fixture
def environ(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path),
        hook.POLICY_VARIABLE: "true",
    }


def set_config(environ: dict[str, str], tmp_path: Path, content: str) -> None:
    path = tmp_path / "uv.toml"
    path.write_text(content, encoding="utf-8")
    environ["UV_CONFIG_FILE"] = str(path)


@pytest.mark.parametrize("policy", ("", "false", "yes", "1", "invalid"))
def test_unconfirmed_or_denied_policy_stops(
    environ: dict[str, str], policy: str, capsys: pytest.CaptureFixture[str]
) -> None:
    environ[hook.POLICY_VARIABLE] = policy
    assert hook.main(environ) == 1
    error = capsys.readouterr().err
    assert hook.POLICY_VARIABLE in error
    assert "docs/deployment.md#docker-build-のpackage-index" in error
    assert "package-api-approved-index" in error
    assert f"azd env set {hook.POLICY_VARIABLE} true" in error
    assert "Functions remote build" in error


def test_missing_policy_is_not_approval(environ: dict[str, str]) -> None:
    del environ[hook.POLICY_VARIABLE]
    assert hook.main(environ) == 1


@pytest.mark.parametrize(
    "config",
    (
        "",
        'cache-dir = "/local/cache"\n',
        '[[index]]\nurl = "https://pypi.org/simple"\ndefault = true\n',
    ),
)
def test_explicit_approval_without_source_conflict_passes(
    environ: dict[str, str], tmp_path: Path, config: str
) -> None:
    set_config(environ, tmp_path, config)
    assert hook.main(environ) == 0


def test_missing_default_config_is_allowed_only_with_approval(
    environ: dict[str, str],
) -> None:
    assert hook.main(environ) == 0


@pytest.mark.parametrize(
    "config",
    (
        '[[index]]\nname = "approved"\nurl = "https://packages.example.test/simple"\ndefault = true\n',
        'index-url = "https://packages.example.test/simple"\n',
        '[pip]\nindex-url = "https://packages.example.test/simple"\n',
        '[[index]]\nurl = "https://pypi.org/simple"\n[[index]]\nurl = "https://packages.example.test/simple"\n',
        '[[index]]\nurl = "http://pypi.org/simple"\n',
        '[[index]]\nurl = "https://pypi.org.example.test/simple"\n',
        '[[index]]\nurl = "https://["\n',
        'index = "invalid"\n',
        '[[index]]\nname = "missing-url"\n',
        '[[index]]\nurl = "https://user:credential-value@pypi.org/simple"\n',
        '[[index]]\nurl = "https://pypi.org/simple?key=credential-value"\n',
        'index = "credential-value\n',
    ),
)
def test_source_conflicts_and_invalid_config_stop_without_exposing_values(
    environ: dict[str, str],
    tmp_path: Path,
    config: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_config(environ, tmp_path, config)
    assert hook.main(environ) == 1
    error = capsys.readouterr().err
    assert "user-level uv" in error
    assert "credential-value" not in error
    assert "packages.example.test" not in error


def test_explicit_missing_config_stops(environ: dict[str, str], tmp_path: Path) -> None:
    environ["UV_CONFIG_FILE"] = str(tmp_path / "missing.toml")
    assert hook.main(environ) == 1


def test_unreadable_config_stops(environ: dict[str, str], tmp_path: Path) -> None:
    environ["UV_CONFIG_FILE"] = str(tmp_path)
    assert hook.main(environ) == 1


@pytest.mark.parametrize("name", ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_FIND_LINKS"))
def test_uv_environment_overrides_stop_without_exposing_values(
    environ: dict[str, str], name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    environ[name] = "https://user:credential-value@packages.example.test/simple"
    assert hook.main(environ) == 1
    error = capsys.readouterr().err
    assert name in error
    assert "credential-value" not in error


def test_user_config_discovery_is_shared(
    environ: dict[str, str], tmp_path: Path
) -> None:
    path = tmp_path / "uv/uv.toml"
    path.parent.mkdir()
    path.write_text(
        '[[index]]\nurl = "https://packages.example.test/simple"\ndefault = true\n',
        encoding="utf-8",
    )
    assert hook.main(environ) == 1


def test_cli_failure_is_nonzero(environ: dict[str, str]) -> None:
    environ[hook.POLICY_VARIABLE] = "false"
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 1
    assert "error: azd up" in result.stderr
    assert not result.stdout


def test_preup_runs_offline_and_cannot_continue_on_error() -> None:
    config = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
    preup = config["hooks"]["preup"]
    for platform, shell in (("windows", "pwsh"), ("posix", "sh")):
        settings = preup[platform]
        assert settings["shell"] == shell
        assert settings["interactive"] is False
        assert settings["continueOnError"] is False
        assert settings["run"].split() == [
            "uv",
            "run",
            "--no-project",
            "--no-config",
            "--offline",
            "--no-python-downloads",
            "scripts/check-public-sources.py",
        ]
    assert config["workflows"]["up"]["steps"][0] == {"azd": "provision base"}
