from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

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
    (tmp_path / "uv.lock").write_text(lock_content, encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(post_edit, "REPO_ROOT", tmp_path)
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


def test_ready_state_adds_no_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / ".venv").mkdir()
    monkeypatch.setattr(
        tasks,
        "user_uv_config_path",
        lambda: pytest.fail("ready state must not inspect user config"),
    )

    tasks.write_approved_index_state("ready")

    assert tasks.approved_index_run_flags() == ["--no-sync"]


def test_changed_lock_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / ".venv").mkdir()
    tasks.write_approved_index_state("ready")
    (tmp_path / "uv.lock").write_text("version = 2\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        tasks.approved_index_run_flags()


def test_missing_environment_provenance_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    (tmp_path / ".venv").mkdir()
    tasks.write_approved_index_state("ready")
    tasks.approved_index_environment_state_path().unlink()

    with pytest.raises(SystemExit):
        tasks.approved_index_run_flags()


def test_incomplete_state_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    state_path = tasks.approved_index_state_path()
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "schema": tasks.APPROVED_INDEX_STATE_VERSION,
                "status": "in-progress",
                "lock_sha256": tasks.lock_sha256(),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        tasks.approved_index_run_flags()


def test_project_environment_honors_relative_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "build/venv")

    assert tasks.project_environment_path() == tmp_path / "build" / "venv"


def test_state_is_stored_outside_virtual_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)

    state_path = tasks.approved_index_state_path()

    assert state_path.parent == tmp_path / tasks.APPROVED_INDEX_STATE_DIRECTORY
    assert tmp_path / ".venv" not in state_path.parents


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


def test_state_free_run_flags_reject_approved_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)

    with pytest.raises(SystemExit):
        tasks.approved_index_run_flags()


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


def test_child_environment_removes_unsafe_uv_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_PROJECT", "/not-a-project")
    monkeypatch.setenv("UV_NO_DEV", "1")
    monkeypatch.setenv("UV_INDEX_APPROVED_INDEX_USERNAME", "username")

    environment = tasks.child_env()

    assert "UV_PROJECT" not in environment
    assert "UV_NO_DEV" not in environment
    assert environment["UV_INDEX_APPROVED_INDEX_USERNAME"] == "username"


def test_package_api_approved_index_uses_buildkit_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "uv.toml"
    write_approved_config(
        config_path, "https://packagefeedproxy.microsoft.io/pypi/simple"
    )
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        tasks,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    tasks.target_package_api_approved_index()

    command = commands[0]
    assert command[:3] == ["docker", "build", "--platform"]
    assert f"id=uv-config,src={config_path}" in command
    assert "UV_INDEX_MODE=approved-index" in command
    assert tasks.API_LOCAL_IMAGE in command


def test_deploy_api_approved_index_uses_prebuilt_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        tasks,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    tasks.target_deploy_api_approved_index()

    assert commands == [
        [
            "azd",
            "deploy",
            "api",
            "--from-package",
            tasks.API_LOCAL_IMAGE,
            "--no-prompt",
        ]
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
    ruff = tmp_path / ".venv" / "bin" / "ruff"
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


def test_lock_change_during_sync_keeps_in_progress_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
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

    state = json.loads(tasks.approved_index_state_path().read_text(encoding="utf-8"))
    assert state["status"] == "in-progress"
    assert pip_environment == {
        "UV_CACHE_DIR": str(tasks.approved_index_cache_path()),
        "UV_CONFIG_FILE": str(config_path),
    }


def test_environment_creation_failure_keeps_in_progress_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_root(monkeypatch, tmp_path)
    config_path = tmp_path / "uv.toml"
    write_approved_config(config_path)
    monkeypatch.setattr(tasks, "user_uv_config_path", lambda: config_path)
    monkeypatch.setattr(tasks, "validate_public_lock", lambda *_args: None)

    def fail_environment_creation(_args: list[str], **_kwargs: object) -> None:
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(tasks, "run", fail_environment_creation)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        tasks.target_sync_dev_approved_index()

    state = json.loads(tasks.approved_index_state_path().read_text(encoding="utf-8"))
    assert state["status"] == "in-progress"


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
        '      version: "0.12.2"\n',
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
        '      version: "0.12.2"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "API_DIR", api_dir)
    monkeypatch.setattr(tasks, "WORKFLOWS_DIR", workflow_path.parent)
    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.3")

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()


def test_check_uv_version_requires_pinned_workflow_version(
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
        '      version: "0.12.3"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "API_DIR", api_dir)
    monkeypatch.setattr(tasks, "WORKFLOWS_DIR", workflow_path.parent)
    monkeypatch.setattr(tasks, "command_output", lambda *args, **kwargs: "uv 0.12.3")

    with pytest.raises(SystemExit):
        tasks.target_check_uv_version()

    workflow_path.write_text(
        "steps:\n"
        "  - uses: astral-sh/setup-uv@sha\n"
        "    env:\n"
        '      version: "0.12.2"\n'
        "    with:\n"
        '      version-file: "pyproject.toml"\n',
        encoding="utf-8",
    )
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
