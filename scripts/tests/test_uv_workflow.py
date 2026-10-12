from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


approved_config = load_module(
    "approved_index_config",
    REPO_ROOT / "scripts" / "approved_index_config.py",
)
public_lock = load_module(
    "public_lock",
    REPO_ROOT / "scripts" / "public_lock.py",
)
tasks = load_module("repository_tasks", REPO_ROOT / "scripts" / "tasks.py")
post_edit = load_module(
    "post_edit_quality_feedback",
    REPO_ROOT / ".github" / "hooks" / "scripts" / "post-edit-quality-feedback.py",
)


@pytest.fixture(autouse=True)
def isolate_uv_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        tasks, "user_uv_config_path", lambda: tmp_path / "missing-uv.toml"
    )
    for name in tasks.UNSAFE_UV_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def configure_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lock_content: str = "version = 1\n"
) -> None:
    (tmp_path / "uv.lock").write_text(lock_content, encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(post_edit, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        tasks,
        "user_uv_config_path",
        lambda: tmp_path / "missing-uv.toml",
    )
    monkeypatch.delenv("UV_CONFIG_FILE", raising=False)
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)


def write_approved_config(
    path: Path, url: str = "https://packages.example.test/simple"
) -> None:
    path.write_text(
        f'[[index]]\nname = "approved-index"\nurl = "{url}"\ndefault = true\n',
        encoding="utf-8",
    )


def test_project_environment_honors_relative_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "build/venv")

    assert tasks.project_environment_path() == tmp_path / "build" / "venv"


@pytest.mark.parametrize(
    ("target", "include_dev", "group_flag"),
    (
        ("target_sync", False, "--no-dev"),
        ("target_sync_dev", True, "--all-groups"),
    ),
)
def test_public_sync_is_locked_configured_and_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target: str,
    include_dev: bool,
    group_flag: str,
) -> None:
    configure_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(tasks, "ensure_public_lock", lambda: None)
    monkeypatch.setattr(tasks, "run", lambda args, **_kwargs: calls.append(args))

    getattr(tasks, target)()

    assert calls == [
        [
            "uv",
            "sync",
            "--project",
            str(tmp_path),
            "--all-packages",
            "--locked",
            "--no-config",
            group_flag,
        ]
    ]
    assert include_dev is (group_flag == "--all-groups")


def test_explicit_public_index_uses_public_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    config_path.write_text(
        '[[index]]\nname = "pypi"\nurl = "https://pypi.org/simple"\ndefault = true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    monkeypatch.setattr(tasks, "ensure_public_lock", lambda: None)
    calls: list[list[str]] = []
    monkeypatch.setattr(tasks, "run", lambda args, **_kwargs: calls.append(args))

    tasks.target_sync()

    assert calls[0][0] == "uv"
    assert "--no-config" in calls[0]
    assert "--locked" in calls[0]
    assert "--no-dev" in calls[0]


def test_private_index_is_selected_only_by_one_default_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    selected: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        tasks,
        "sync_private_index",
        lambda path, *, include_dev: selected.append((path, include_dev)),
    )

    tasks.target_sync_dev()

    assert selected == [(config_path, True)]


@pytest.mark.parametrize(
    "content",
    (
        "[[index]\n",
        '[[index]]\nname = "approved"\nurl = "https://packages.example.test/simple"\n',
        '[[index]]\nname = "one"\nurl = "https://packages.example.test/simple"\ndefault = true\n'
        '[[index]]\nname = "two"\nurl = "https://mirror.example.test/simple"\ndefault = true\n',
        'index-url = "https://packages.example.test/simple"\n',
        "[pip]\n",
        '[[index]]\nname = "approved"\nurl = "http://packages.example.test/simple"\ndefault = true\n',
        '[[index]]\nname = "approved"\nurl = "https://[ "\ndefault = true\n',
    ),
)
def test_invalid_registry_configuration_stops_before_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    config_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    monkeypatch.setattr(
        tasks, "run", lambda *_args, **_kwargs: pytest.fail("uv must not run")
    )
    monkeypatch.setattr(
        tasks,
        "sync_private_index",
        lambda *_args, **_kwargs: pytest.fail("private sync must not run"),
    )

    with pytest.raises(SystemExit):
        tasks.target_sync_dev()


def test_explicit_missing_config_stops_before_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv("UV_CONFIG_FILE", str(missing))
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: missing)
    monkeypatch.setattr(
        tasks, "run", lambda *_args, **_kwargs: pytest.fail("uv must not run")
    )

    with pytest.raises(SystemExit, match="not found"):
        tasks.target_sync_dev()


def test_public_sync_failure_does_not_fall_back_and_prints_fixed_guidance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_root(monkeypatch, tmp_path)
    monkeypatch.setattr(tasks, "ensure_public_lock", lambda: None)
    monkeypatch.setattr(
        tasks,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit(2)),
    )
    monkeypatch.setattr(
        tasks,
        "sync_private_index",
        lambda *_args, **_kwargs: pytest.fail("must not fall back"),
    )

    with pytest.raises(SystemExit) as raised:
        tasks.target_sync_dev()

    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "public PyPI was selected" in error
    assert "No automatic switch to a private index" in error
    assert "default = true" in error
    assert "password" not in error.lower()
    assert "packages.example.test" not in error


def test_private_index_sync_hides_installer_output_and_limits_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_USERNAME", "secret-user")
    monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_PASSWORD", "fixture-value")
    monkeypatch.setenv("UV_INDEX_OTHER_PASSWORD", "other-secret")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def subprocess_run(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(tasks.subprocess, "run", subprocess_run)

    tasks.run_private_index_sync(["uv", "pip", "sync"], config_path=config_path)

    assert calls[0][0] == tasks.resolve_command(["uv", "pip", "sync"])
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["env"] == tasks.child_env(
        {
            "UV_CACHE_DIR": str(tasks.approved_index_cache_path()),
            "UV_CONFIG_FILE": str(config_path),
            "UV_INDEX_APPROVED_INDEX_USERNAME": os.environ[
                "UV_INDEX_APPROVED_INDEX_USERNAME"
            ],
            "UV_INDEX_APPROVED_INDEX_PASSWORD": os.environ[
                "UV_INDEX_APPROVED_INDEX_PASSWORD"
            ],
        }
    )
    assert capsys.readouterr().err == ""


def test_private_index_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)

    def failed_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args, 1, b"https://packages.example.test/secret", b"fixture-value"
        )

    monkeypatch.setattr(tasks.subprocess, "run", failed_run)

    with pytest.raises(SystemExit) as raised:
        tasks.run_private_index_sync(["uv", "pip", "sync"], config_path=config_path)

    error = capsys.readouterr().err
    assert raised.value.code == 1
    assert "Approved-index synchronization failed" in error
    assert "fixture-value" not in error
    assert "packages.example.test" not in error


def test_private_sync_failure_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "selected_approved_index_config", lambda: config_path)
    monkeypatch.setattr(tasks, "ensure_public_lock", lambda: None)
    monkeypatch.setattr(
        tasks,
        "sync_private_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit(1)),
    )
    monkeypatch.setattr(
        tasks, "run", lambda *_args, **_kwargs: pytest.fail("public sync must not run")
    )

    with pytest.raises(SystemExit) as raised:
        tasks.sync_dependencies(include_dev=True)

    assert raised.value.code == 1


@pytest.mark.parametrize("failure", ("auth", "missing-artifact", "hash-mismatch"))
def test_private_sync_fails_against_local_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    artifact = b"not-a-valid-wheel"
    artifact_hash = hashlib.sha256(artifact).hexdigest()
    authorization_headers: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if failure == "auth":
                authorization_headers.append(self.headers.get("Authorization"))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="test"')
                self.end_headers()
                return
            if self.path.startswith("/simple/fixture-package"):
                body = (
                    '<a href="/packages/fixture_package-1.0.0-py3-none-any.whl'
                    f'#sha256={artifact_hash}">wheel</a>'
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/packages/"):
                if failure == "missing-artifact":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(artifact)))
                self.end_headers()
                self.wfile.write(artifact)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        config_path = tmp_path / "uv.toml"
        index_url = f"http://127.0.0.1:{server.server_port}/simple"
        authentication = 'authenticate = "always"\n' if failure == "auth" else ""
        config_path.write_text(
            '[[index]]\nname = "approved-index"\n'
            f'url = "{index_url}"\ndefault = true\n{authentication}',
            encoding="utf-8",
        )
        expected_hash = "0" * 64 if failure == "hash-mismatch" else artifact_hash
        requirements = tmp_path / "requirements.txt"
        requirements.write_text(
            f"fixture-package==1.0.0 --hash=sha256:{expected_hash}\n",
            encoding="utf-8",
        )
        if failure == "auth":
            monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_USERNAME", "fixture-user")
            monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_PASSWORD", "fixture-password")

        with pytest.raises(SystemExit) as raised:
            tasks.run_private_index_sync(
                [
                    "uv",
                    "pip",
                    "sync",
                    "--require-hashes",
                    str(requirements),
                    "--python",
                    sys.executable,
                ],
                config_path=config_path,
            )

        assert raised.value.code != 0
        error = capsys.readouterr().err
        assert "Approved-index synchronization failed" in error
        assert "fixture-password" not in error
        if failure == "auth":
            assert any(authorization_headers)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


def test_approved_index_config_requires_one_non_public_default(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)

    approved_config.validate_approved_index_config(config_path, {})

    write_approved_config(config_path, "https://pypi.org/simple")
    with pytest.raises(approved_config.ApprovedIndexConfigError):
        approved_config.validate_approved_index_config(config_path, {})

    write_approved_config(config_path, "https://pypi.org./simple")
    with pytest.raises(approved_config.ApprovedIndexConfigError):
        approved_config.validate_approved_index_config(config_path, {})


def test_approved_index_config_rejects_source_environment_override(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)

    with pytest.raises(approved_config.ApprovedIndexConfigError):
        approved_config.validate_approved_index_config(
            config_path,
            {"UV_DEFAULT_INDEX": "https://packages.example.test/simple"},
        )

    with pytest.raises(approved_config.ApprovedIndexConfigError):
        approved_config.validate_approved_index_config(
            config_path,
            {"UV_FIND_LINKS": "https://packages.example.test/wheels"},
        )
    for variable in (
        "UV_BUILD_CONSTRAINT",
        "UV_INSECURE_HOST",
        "UV_NO_CONFIG",
        "UV_NO_DEV",
        "UV_NO_VERIFY_HASHES",
        "UV_PROJECT",
        "UV_WORKING_DIR",
    ):
        with pytest.raises(approved_config.ApprovedIndexConfigError):
            approved_config.validate_approved_index_config(
                config_path,
                {variable: "1"},
            )


def test_child_environment_removes_unsafe_uv_overrides_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_PROJECT", "/not-a-project")
    monkeypatch.setenv("UV_NO_DEV", "1")
    monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_USERNAME", "username")

    environment = tasks.child_env()

    assert "UV_PROJECT" not in environment
    assert "UV_NO_DEV" not in environment
    assert "UV_INDEX_APPROVED_INDEX_USERNAME" not in environment


def test_package_api_approved_index_delegates_validated_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "uv.toml"
    write_approved_config(
        config_path, "https://packagefeedproxy.microsoft.io/pypi/simple"
    )
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    import api_artifact

    calls: list[tuple[Path, Path, str]] = []
    lock_checks: list[None] = []
    monkeypatch.setattr(
        tasks,
        "ensure_public_lock",
        lambda: lock_checks.append(None),
    )
    reference = "aks-chaos-lab-approved:sha256-" + "a" * 64

    def build(root: Path, config: Path, digest: str) -> str:
        calls.append((root, config, digest))
        return reference

    monkeypatch.setattr(api_artifact, "build_api_image", build)
    tasks.main(["package-api-approved-index"])
    assert calls == [
        (tasks.ROOT, config_path, approved_config.config_sha256(config_path))
    ]
    assert lock_checks == [None]


def test_deploy_api_approved_index_cli_requires_explicit_image() -> None:
    with pytest.raises(SystemExit) as raised:
        tasks.main(["deploy-api-approved-index"])
    assert raised.value.code == 2


def test_deploy_api_approved_index_cli_passes_explicit_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images: list[str] = []
    monkeypatch.setattr(
        tasks,
        "target_deploy_api_approved_index",
        images.append,
    )
    reference = "aks-chaos-lab-approved:sha256-" + "a" * 64
    tasks.main(["deploy-api-approved-index", "--image", reference])
    assert images == [reference]


@pytest.mark.parametrize("kubeconfig", [None, "", "/prepared/cluster.kubeconfig"])
def test_deploy_api_uses_fresh_published_image_and_checks_running_artifact(
    monkeypatch: pytest.MonkeyPatch, kubeconfig: str | None
) -> None:
    import api_artifact

    if kubeconfig is None:
        monkeypatch.delenv("KUBECONFIG", raising=False)
    else:
        monkeypatch.setenv("KUBECONFIG", kubeconfig)
    monkeypatch.setenv("AZURE_ENV_NAME", "eval")
    monkeypatch.setenv("SERVICE_API_IMAGE_NAME", "old.example.test/api:old")
    calls: list[str] = []
    commands: list[list[str]] = []
    local = "sha256:" + "a" * 64
    remote = "registry.example.test/api:new"
    published = api_artifact.PublishedApiImage(remote, local, "sha256:" + "b" * 64)
    budgets: list[object] = []

    def output(command: list[str], **kwargs: Any) -> str:
        assert command[-2:] == ["-e", "eval"]
        calls.append(command[3])
        if command[3] == "SERVICE_API_IMAGE_NAME":
            budgets.append(kwargs["deadline"])
            return remote
        return "eval"

    def inspect(
        image_id: str, reference: str, *, deadline: object
    ) -> api_artifact.PublishedApiImage:
        assert deadline is budgets[0]
        assert (image_id, reference) == (local, remote)
        calls.append("registry")
        return published

    class Connection:
        def rollout(self, *args: object) -> None:
            assert args == ("chaos-app", "chaos-lab", 600)
            calls.append("rollout")

        def json_resource(self, args: list[str]) -> dict[str, str]:
            calls.append(args[1])
            assert args[args.index("-n") : args.index("-n") + 2] == ["-n", "chaos-lab"]
            return {"kind": args[1]}

    @contextmanager
    def connection(**kwargs: Any) -> Iterator[Connection]:
        assert kwargs["deadline"] is budgets[0]
        calls.append("connect")
        yield Connection()

    def deploy(command: list[str]) -> None:
        assert os.environ.get("KUBECONFIG") == kubeconfig
        commands.append(command)
        calls.append("deploy")

    def verify(
        image: api_artifact.PublishedApiImage, *resources: dict[str, str]
    ) -> None:
        assert image is published
        assert resources == (
            {"kind": "deployment"},
            {"kind": "pods"},
            {"kind": "replicasets"},
        )
        calls.append("verify")

    monkeypatch.setattr(api_artifact, "inspect_local_image", lambda _: local)
    monkeypatch.setattr(api_artifact, "inspect_published_image", inspect)
    monkeypatch.setattr(api_artifact, "verify_running_api", verify)
    monkeypatch.setattr(tasks, "run_aks_command", output)
    monkeypatch.setattr(tasks, "aks_connection", connection)
    monkeypatch.setattr(tasks, "run", deploy)
    tasks.target_deploy_api_approved_index("explicit-image")
    assert commands == [
        [
            "azd",
            "deploy",
            "api",
            "-e",
            "eval",
            "--from-package",
            "explicit-image",
            "--no-prompt",
        ]
    ]
    assert calls == [
        "AZURE_ENV_NAME",
        "deploy",
        "SERVICE_API_IMAGE_NAME",
        "registry",
        "connect",
        "rollout",
        "deployment",
        "replicasets",
        "pods",
        "verify",
    ]


@pytest.mark.parametrize("stage", ["local", "deploy", "published"])
def test_api_deploy_stops_on_artifact_or_deployment_failure(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    import api_artifact

    calls: list[str] = []
    monkeypatch.setattr(tasks, "deployment_environment_name", lambda _: "eval")

    def local(_: str) -> str:
        if stage == "local":
            raise api_artifact.ApiArtifactError("missing local image")
        return "sha256:" + "a" * 64

    def deploy(_: list[str]) -> None:
        calls.append("deploy")
        if stage == "deploy":
            raise SystemExit(3)

    def published(*_args: object, **_kwargs: object) -> None:
        calls.append("published")
        raise api_artifact.ApiArtifactError("published digest mismatch")

    monkeypatch.setattr(api_artifact, "inspect_local_image", local)
    monkeypatch.setattr(api_artifact, "inspect_published_image", published)
    monkeypatch.setattr(tasks, "run", deploy)
    monkeypatch.setattr(tasks, "run_aks_command", lambda *_args, **_kwargs: "remote")
    monkeypatch.setattr(
        tasks, "aks_connection", lambda **_: pytest.fail("unexpected AKS")
    )
    with pytest.raises(SystemExit):
        tasks.target_deploy_api_approved_index("explicit-image")
    assert (
        calls
        == {
            "local": [],
            "deploy": ["deploy"],
            "published": ["deploy", "published"],
        }[stage]
    )


def test_nap_defaults_are_enabled_across_infrastructure_and_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", raising=False)
    assert tasks.node_provisioning_enabled() is True
    parameters = json.loads(
        (REPO_ROOT / "infra/main.parameters.json").read_text(encoding="utf-8")
    )
    assert parameters["parameters"]["enableNodeAutoProvisioning"]["value"] == (
        "${AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING:true}"
    )
    for path in ("infra/main.bicep", "infra/modules/aks.bicep"):
        assert "param enableNodeAutoProvisioning bool = true" in (
            REPO_ROOT / path
        ).read_text(encoding="utf-8")
    main = (REPO_ROOT / "infra/main.bicep").read_text(encoding="utf-8")
    assert "enableNodeAutoProvisioning: enableNodeAutoProvisioning" in main


@pytest.mark.parametrize("flag", ["false", "FALSE", "", "invalid", "1"])
def test_nap_disabled_or_invalid_never_connects(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setattr(tasks, "deployment_environment_name", lambda _: "eval")
    monkeypatch.setenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", flag)
    monkeypatch.setattr(
        tasks, "aks_connection", lambda **_: pytest.fail("unexpected AKS")
    )
    monkeypatch.setattr(tasks, "run", lambda _: pytest.fail("unexpected deploy"))
    if flag in ("false", "FALSE"):
        tasks.target_deploy_node_provisioning()
    else:
        with pytest.raises(SystemExit):
            tasks.target_deploy_node_provisioning()


@pytest.mark.parametrize("fail_wait", [False, True])
@pytest.mark.parametrize("kubeconfig", [None, "/prepared/cluster.kubeconfig"])
@pytest.mark.parametrize("flag", [None, "true", "TRUE"])
def test_nap_waits_then_deploys_only_its_service(
    monkeypatch: pytest.MonkeyPatch,
    fail_wait: bool,
    kubeconfig: str | None,
    flag: str | None,
) -> None:
    if flag is None:
        monkeypatch.delenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", raising=False)
    else:
        monkeypatch.setenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", flag)
    if kubeconfig is None:
        monkeypatch.delenv("KUBECONFIG", raising=False)
    else:
        monkeypatch.setenv("KUBECONFIG", kubeconfig)
    calls: list[list[str]] = []
    budgets: list[object] = []

    def environment(deadline: object) -> str:
        budgets.append(deadline)
        return "eval"

    class Connection:
        def kubectl(self, args: list[str]) -> str:
            calls.append(args[:-1])
            assert args[-1].startswith("--timeout=")
            if fail_wait:
                raise tasks.AKSConnectionError("CRD wait failed")
            return "condition met"

    @contextmanager
    def connection(**kwargs: Any) -> Iterator[Connection]:
        assert kwargs["deadline"] is budgets[0]
        yield Connection()
        calls.append(["cleanup"])

    def deploy(command: list[str]) -> None:
        assert os.environ.get("KUBECONFIG") == kubeconfig
        assert calls[-1] == ["cleanup"]
        calls.append(command)

    monkeypatch.setattr(tasks, "deployment_environment_name", environment)
    monkeypatch.setattr(tasks, "aks_connection", connection)
    monkeypatch.setattr(tasks, "run", deploy)
    if fail_wait:
        with pytest.raises(SystemExit):
            tasks.target_deploy_node_provisioning()
        assert len(calls) == 1
    else:
        tasks.target_deploy_node_provisioning()
        assert calls == [
            ["wait", "--for=create", "crd/nodepools.karpenter.sh"],
            ["wait", "--for=condition=Established", "crd/nodepools.karpenter.sh"],
            ["wait", "--for=create", "crd/aksnodeclasses.karpenter.azure.com"],
            [
                "wait",
                "--for=condition=Established",
                "crd/aksnodeclasses.karpenter.azure.com",
            ],
            ["cleanup"],
            ["azd", "deploy", "node-provisioning", "-e", "eval", "--no-prompt"],
        ]


@pytest.mark.parametrize("name,actual", [(None, ""), ("eval", ""), ("eval", "another")])
@pytest.mark.parametrize("flag", [None, "false"])
def test_nap_requires_resolved_environment(
    monkeypatch: pytest.MonkeyPatch,
    name: str | None,
    actual: str,
    flag: str | None,
) -> None:
    if name is None:
        monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    else:
        monkeypatch.setenv("AZURE_ENV_NAME", name)
    if flag is None:
        monkeypatch.delenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", raising=False)
    else:
        monkeypatch.setenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", flag)
    monkeypatch.setattr(tasks, "run_aks_command", lambda *_args, **_kwargs: actual)
    monkeypatch.setattr(
        tasks, "aks_connection", lambda **_: pytest.fail("unexpected AKS")
    )
    with pytest.raises(SystemExit):
        tasks.target_deploy_node_provisioning()


def test_up_preserves_sequential_services_with_one_nap_step() -> None:
    import yaml

    config = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text(encoding="utf-8"))
    assert config["workflows"]["up"]["steps"] == [
        {"azd": "provision base"},
        {"azd": "deploy api-instrumentation"},
        {
            "azd": {
                "args": [
                    "exec",
                    "--",
                    "uv",
                    "run",
                    "--no-project",
                    "scripts/tasks.py",
                    "deploy-node-provisioning",
                ]
            }
        },
        {"azd": "deploy api"},
        {"azd": "deploy observability"},
        {"azd": "deploy chaos-mesh"},
        {"azd": "deploy external-sli-publisher"},
        {"azd": "provision sli"},
    ]


def test_run_uv_binds_root_project_and_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def capture_run(
        args: list[str],
        *,
        env: dict[str, str],
        **_kwargs: object,
    ) -> None:
        calls.append((args, env))

    monkeypatch.setattr(tasks, "run", capture_run)

    tasks.run_uv(["ruff", "check"])

    assert calls == [
        (
            [
                "uv",
                "run",
                "--project",
                str(tmp_path),
                "--no-sync",
                "ruff",
                "check",
            ],
            {"UV_PROJECT_ENVIRONMENT": str(tmp_path / ".venv")},
        )
    ]


def test_post_edit_invokes_project_ruff_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    ruff = post_edit.project_ruff_path()
    ruff.parent.mkdir(parents=True)
    ruff.touch()
    source_path = tmp_path / "example.py"
    source_path.write_text("value = 1\n", encoding="utf-8")
    commands: list[list[str]] = []

    def capture_command(
        command: list[str],
        **_kwargs: object,
    ) -> object:
        commands.append(command)
        return post_edit.CommandResult(command, 0, "", "")

    monkeypatch.setattr(post_edit, "run_command", capture_command)

    post_edit.process_python(source_path)

    assert len(commands) == 2
    assert commands == [
        [str(ruff), "check", "--fix", "--quiet", str(source_path)],
        [str(ruff), "format", "--quiet", str(source_path)],
    ]


def test_post_edit_reports_missing_project_ruff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    source_path = tmp_path / "example.py"
    source_path.write_text("value = 1\n", encoding="utf-8")

    messages = post_edit.process_python(source_path)

    assert messages == [
        "The project virtual environment does not contain ruff. Run the "
        "appropriate sync target before editing Python files."
    ]


def test_approved_index_config_rejects_find_links_setting(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "uv.toml"
    config_path.write_text(
        'find-links = ["https://packages.example.test/wheels"]\n'
        '[[index]]\nname = "approved-index"\n'
        'url = "https://packages.example.test/simple"\ndefault = true\n',
        encoding="utf-8",
    )

    with pytest.raises(approved_config.ApprovedIndexConfigError):
        approved_config.validate_approved_index_config(config_path, {})


def test_approved_index_config_rejects_pip_table(tmp_path: Path) -> None:
    config_path = tmp_path / "uv.toml"
    config_path.write_text(
        "[pip]\nverify-hashes = false\n"
        '[[index]]\nname = "approved-index"\n'
        'url = "https://packages.example.test/simple"\ndefault = true\n',
        encoding="utf-8",
    )

    with pytest.raises(approved_config.ApprovedIndexConfigError):
        approved_config.validate_approved_index_config(config_path, {})


@pytest.mark.parametrize(
    ("include_dev", "group_flag"),
    ((False, "--no-dev"), (True, "--all-groups")),
)
def test_private_sync_uses_frozen_hash_requirements_and_preserves_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    include_dev: bool,
    group_flag: str,
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    commands: list[list[str]] = []
    private_syncs: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(tasks, "ensure_public_lock", lambda: None)
    monkeypatch.setattr(tasks, "lock_sha256", lambda: "same-lock")
    monkeypatch.setattr(tasks, "run", lambda args, **_kwargs: commands.append(args))
    monkeypatch.setattr(tasks, "validate_exported_requirements", lambda _path: None)
    monkeypatch.setattr(
        tasks,
        "run_private_index_sync",
        lambda args, *, config_path: private_syncs.append((args, config_path)),
    )

    tasks.sync_private_index(config_path, include_dev=include_dev)

    assert commands[0] == [
        "uv",
        "venv",
        "--clear",
        "--no-config",
        "--python",
        "3.14",
        str(tmp_path / ".venv"),
    ]
    assert commands[1][:8] == [
        "uv",
        "export",
        "--project",
        str(tmp_path),
        "--quiet",
        "--frozen",
        "--no-config",
        "--all-packages",
    ]
    assert group_flag in commands[1]
    assert "--no-emit-workspace" in commands[1]
    assert "--no-emit-index-url" in commands[1]
    assert commands[1][-2] == "--output-file"
    assert private_syncs[0][0][:3] == ["uv", "pip", "sync"]
    assert "--require-hashes" in private_syncs[0][0]
    assert private_syncs[0][0][-2:] == [
        "--python",
        str(tmp_path / ".venv/bin/python"),
    ]
    assert private_syncs[0][1] == config_path
    assert (
        tmp_path / ".venv/lib/python3.14/site-packages/aks-chaos-lab-workspace.pth"
    ).is_file()


def test_private_sync_fails_if_lock_changes_during_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    lock_digests = iter(("before", "after"))
    monkeypatch.setattr(tasks, "ensure_public_lock", lambda: None)
    monkeypatch.setattr(tasks, "lock_sha256", lambda: next(lock_digests))
    monkeypatch.setattr(tasks, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "run_private_index_sync", lambda *_args, **_kwargs: None)

    with pytest.raises(SystemExit):
        tasks.sync_private_index(config_path, include_dev=True)


def test_public_lock_rejects_direct_url_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(
        monkeypatch,
        tmp_path,
        (
            "version = 1\n"
            '[[package]]\nname = "unsafe"\nversion = "1.0.0"\n'
            'source = { url = "https://example.test/unsafe.whl" }\n'
        ),
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = []\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        tasks.target_check_public_lock()


@pytest.mark.parametrize(
    "content",
    (
        "unsafe @ https://example.test/unsafe.whl\n",
        "safe==1.0.0\n",
        f"--hash=sha256:{'0' * 64}\n",
        "safe==1.0.0 --hash=sha256:invalid\n",
    ),
)
def test_private_exported_requirements_reject_sources_or_missing_hashes(
    tmp_path: Path,
    content: str,
) -> None:
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text(content, encoding="utf-8")

    with pytest.raises(public_lock.PublicLockError):
        public_lock.validate_exported_requirements(requirements_path)


def test_private_exported_requirements_accept_hash_pinned_registry_package(
    tmp_path: Path,
) -> None:
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text(
        f"safe==1.0.0 \\\n    --hash=sha256:{'0' * 64}\n",
        encoding="utf-8",
    )

    public_lock.validate_exported_requirements(requirements_path)


def test_check_uv_version_allows_any_host_at_or_above_the_lower_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2"\n',
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "Dockerfile").write_text(
        "FROM astral/uv:0.12.2 AS uv\n",
        encoding="utf-8",
    )
    workflow_path = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(
        "steps:\n"
        "  - uses: astral-sh/setup-uv@sha\n"
        "    with:\n"
        '      resolution-strategy: "lowest"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "API_DIR", api_dir)
    monkeypatch.setattr(tasks, "WORKFLOWS_DIR", workflow_path.parent)
    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.2")

    tasks.target_check_uv_version()

    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.3")
    tasks.target_check_uv_version()

    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.13.0")
    tasks.target_check_uv_version()

    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.1")
    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()


def test_check_uv_version_requires_pinned_docker_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2"\n',
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "Dockerfile").write_text(
        "# old image: astral/uv:0.12.2\nFROM astral/uv:0.12.3 AS uv\n",
        encoding="utf-8",
    )
    workflow_path = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(
        "steps:\n"
        "  - uses: astral-sh/setup-uv@sha\n"
        "    with:\n"
        '      resolution-strategy: "lowest"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "API_DIR", api_dir)
    monkeypatch.setattr(tasks, "WORKFLOWS_DIR", workflow_path.parent)
    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.3")

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()


@pytest.mark.parametrize(
    "workflow_settings",
    (
        '      version: "0.12.2"\n      resolution-strategy: "lowest"\n',
        '      version: "0.12.2" # explicit pin\n      resolution-strategy: "lowest"\n',
        '      version-file: "pyproject.toml"\n      resolution-strategy: "lowest"\n',
        '      resolution-strategy: "highest"\n',
        '      resolution-strategy: "lowest"\n      working-directory: "src/api"\n',
        "",
    ),
)
def test_check_uv_version_requires_workflow_to_resolve_pyproject_lower_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workflow_settings: str,
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2"\n',
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "Dockerfile").write_text(
        "FROM astral/uv:0.12.2 AS uv\n",
        encoding="utf-8",
    )
    workflow_path = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(
        f"steps:\n  - uses: astral-sh/setup-uv@sha\n    with:\n{workflow_settings}",
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "API_DIR", api_dir)
    monkeypatch.setattr(tasks, "WORKFLOWS_DIR", workflow_path.parent)
    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.3")

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()


def test_check_uv_version_rejects_an_upper_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2,<0.13.0"\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()


def test_check_uv_version_rejects_root_uv_toml_version_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.toml").write_text(
        'required-version = ">=0.12.3"\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()
