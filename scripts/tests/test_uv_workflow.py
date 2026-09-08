from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
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


def configure_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lock_content: str = "version = 1\n"
) -> None:
    tasks.release_approved_index_lock()
    (tmp_path / "uv.lock").write_text(lock_content, encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(post_edit, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tasks, "_approved_index_environment_prepared", False)
    monkeypatch.setattr(
        tasks,
        "user_uv_config_path",
        lambda: tmp_path / "missing-uv.toml",
    )
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)


def write_approved_config(
    path: Path, url: str = "https://packages.example.test/simple"
) -> None:
    path.write_text(
        f'[[index]]\nname = "approved-index"\nurl = "{url}"\ndefault = true\n',
        encoding="utf-8",
    )


def test_approved_index_run_flags_sync_once_per_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    sync_calls = 0

    def sync_approved_index() -> None:
        nonlocal sync_calls
        sync_calls += 1
        monkeypatch.setattr(tasks, "_approved_index_environment_prepared", True)

    monkeypatch.setattr(
        tasks,
        "target_sync_dev_approved_index",
        sync_approved_index,
    )

    assert tasks.approved_index_run_flags() == ["--no-sync"]
    assert tasks.approved_index_run_flags() == ["--no-sync"]
    assert sync_calls == 1


def test_approved_index_run_flags_defer_missing_config_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)

    def missing_config_path() -> Path:
        raise approved_config.ApprovedIndexConfigError("missing config root")

    monkeypatch.setattr(tasks, "user_uv_config_path", missing_config_path)

    assert tasks.approved_index_run_flags() == []


def test_project_environment_honors_relative_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "build/venv")

    assert tasks.project_environment_path() == tmp_path / "build" / "venv"


def test_approved_index_lock_is_keyed_by_absolute_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    shared_environment = tmp_path / "shared" / ".venv"
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(shared_environment))

    first_lock = tasks.approved_index_lock_path()
    monkeypatch.setattr(tasks, "ROOT", tmp_path / "other-worktree")

    assert tasks.approved_index_lock_path() == first_lock


@pytest.mark.parametrize(
    "target_name",
    ("target_install", "target_sync", "target_sync_dev"),
)
def test_standard_sync_targets_reject_approved_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_name: str,
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    monkeypatch.setattr(
        tasks,
        "run",
        lambda *_args, **_kwargs: pytest.fail("uv must not run"),
    )

    with pytest.raises(SystemExit):
        getattr(tasks, target_name)()


@pytest.mark.parametrize(
    "content",
    (
        None,
        '[[index]]\nname = "pypi"\nurl = "https://pypi.org/simple"\ndefault = true\n',
        "[[index]\n",
    ),
)
def test_non_approved_config_is_deferred_to_uv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str | None,
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    if content is not None:
        config_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)

    tasks.ensure_approved_index_not_selected()
    assert tasks.approved_index_run_flags() == []


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
    monkeypatch.setenv(tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE, "1")

    environment = tasks.child_env()

    assert "UV_PROJECT" not in environment
    assert "UV_NO_DEV" not in environment
    assert "UV_INDEX_APPROVED_INDEX_USERNAME" not in environment
    assert tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE not in environment

    review_environment = tasks.child_env(
        {tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE: "1"}
    )
    assert review_environment[tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE] == "1"


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
    repairs: list[bool] = []
    monkeypatch.setattr(
        tasks,
        "ensure_public_lock",
        lambda *, allow_repair: repairs.append(allow_repair),
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
    assert repairs == [True]


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


@pytest.mark.parametrize("flag", [None, "false", "FALSE", "", "invalid", "1"])
def test_nap_disabled_or_invalid_never_connects(
    monkeypatch: pytest.MonkeyPatch, flag: str | None
) -> None:
    monkeypatch.setattr(tasks, "deployment_environment_name", lambda _: "eval")
    if flag is None:
        monkeypatch.delenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", raising=False)
    else:
        monkeypatch.setenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", flag)
    monkeypatch.setattr(
        tasks, "aks_connection", lambda **_: pytest.fail("unexpected AKS")
    )
    monkeypatch.setattr(tasks, "run", lambda _: pytest.fail("unexpected deploy"))
    if flag in (None, "false", "FALSE"):
        tasks.target_deploy_node_provisioning()
    else:
        with pytest.raises(SystemExit):
            tasks.target_deploy_node_provisioning()


@pytest.mark.parametrize("fail_wait", [False, True])
@pytest.mark.parametrize("kubeconfig", [None, "/prepared/cluster.kubeconfig"])
def test_nap_waits_then_deploys_only_its_service(
    monkeypatch: pytest.MonkeyPatch, fail_wait: bool, kubeconfig: str | None
) -> None:
    monkeypatch.setenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", "true")
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
def test_nap_requires_resolved_environment_even_when_disabled(
    monkeypatch: pytest.MonkeyPatch, name: str | None, actual: str
) -> None:
    if name is None:
        monkeypatch.delenv("AZURE_ENV_NAME", raising=False)
    else:
        monkeypatch.setenv("AZURE_ENV_NAME", name)
    monkeypatch.delenv("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING", raising=False)
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


def test_lock_change_during_sync_fails_before_environment_is_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    monkeypatch.setattr(tasks, "acquire_approved_index_lock", lambda: None)
    monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_USERNAME", "username")
    monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_PASSWORD", "password")
    monkeypatch.setenv("UV_INDEX_OTHER_PASSWORD", "other-password")
    monkeypatch.setattr(tasks, "validate_public_lock", lambda *_args: None)
    monkeypatch.setattr(
        tasks,
        "validate_exported_requirements",
        lambda *_args: None,
    )
    pip_environment: dict[str, str] = {}

    def change_lock_after_install(args: list[str], **kwargs: object) -> None:
        if args[:3] == ["uv", "pip", "sync"]:
            env = kwargs.get("env")
            assert isinstance(env, dict)
            pip_environment.update(
                {str(name): str(value) for name, value in env.items()}
            )
            (tmp_path / "uv.lock").write_text("version = 2\n", encoding="utf-8")

    monkeypatch.setattr(tasks, "run", change_lock_after_install)

    with pytest.raises(SystemExit):
        tasks.target_sync_dev_approved_index()

    assert tasks.__dict__["_approved_index_environment_prepared"] is False
    assert pip_environment == {
        "UV_CACHE_DIR": str(tasks.approved_index_cache_path()),
        "UV_CONFIG_FILE": str(config_path),
        "UV_INDEX_APPROVED_INDEX_USERNAME": "username",
        "UV_INDEX_APPROVED_INDEX_PASSWORD": "password",
    }


def test_environment_creation_failure_does_not_mark_process_prepared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    monkeypatch.setattr(tasks, "acquire_approved_index_lock", lambda: None)
    monkeypatch.setattr(tasks, "validate_public_lock", lambda *_args: None)

    def fail_environment_creation(_args: list[str], **_kwargs: object) -> None:
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(tasks, "run", fail_environment_creation)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        tasks.target_sync_dev_approved_index()

    assert tasks.__dict__["_approved_index_environment_prepared"] is False


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


def test_exported_requirements_reject_direct_source(tmp_path: Path) -> None:
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text(
        "unsafe @ https://example.test/unsafe.whl\n",
        encoding="utf-8",
    )

    with pytest.raises(public_lock.PublicLockError):
        public_lock.validate_exported_requirements(requirements_path)


def test_exported_requirements_require_sha256_hash(tmp_path: Path) -> None:
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text("safe==1.0.0\n", encoding="utf-8")

    with pytest.raises(public_lock.PublicLockError):
        public_lock.validate_exported_requirements(requirements_path)

    requirements_path.write_text(
        f"safe==1.0.0 \\\n    --hash=sha256:{'0' * 64}\n",
        encoding="utf-8",
    )
    public_lock.validate_exported_requirements(requirements_path)


def test_check_uv_version_allows_compatible_host_patch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2,<0.13.0"\n',
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "Dockerfile").write_text(
        "FROM ghcr.io/astral-sh/uv:0.12.2 AS uv\n",
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
    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()

    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.1")
    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()


def test_check_uv_version_requires_pinned_docker_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2,<0.13.0"\n',
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "Dockerfile").write_text(
        "# old image: ghcr.io/astral-sh/uv:0.12.2\n"
        "FROM ghcr.io/astral-sh/uv:0.12.3 AS uv\n",
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
        '[tool.uv]\nrequired-version = ">=0.12.2,<0.13.0"\n',
        encoding="utf-8",
    )
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "Dockerfile").write_text(
        "FROM ghcr.io/astral-sh/uv:0.12.2 AS uv\n",
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


def test_check_uv_version_requires_one_minor_compatibility_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = "==0.12.2"\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()


def test_check_uv_version_rejects_root_uv_toml_version_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-version = ">=0.12.2,<0.13.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.toml").write_text(
        'required-version = ">=0.12.3,<0.13.0"\n',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()
