from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


aks = load_module("aks_connection", "aks_connection.py")
readiness = load_module("aks_readiness_hook", "wait-for-aks-readiness.py")
otel = load_module("aks_otel_hook", "check-api-otel-injection.py")


def target_environment(name: str = "a") -> dict[str, str]:
    return {
        "AZURE_ENV_NAME": name,
        "AZURE_SUBSCRIPTION_ID": f"subscription-{name}",
        "AZURE_RESOURCE_GROUP": f"rg-{name}",
        "AZURE_AKS_CLUSTER_NAME": f"aks-{name}",
    }


def configuration(name: str = "a", login: str = "azd") -> dict[str, Any]:
    return {
        "current-context": "same-context-name",
        "contexts": [
            {"name": "same-context-name", "context": {"cluster": name, "user": name}}
        ],
        "clusters": [
            {"name": name, "cluster": {"server": f"https://{name}.aks.example"}}
        ],
        "users": [
            {
                "name": name,
                "user": {
                    "exec": {
                        "command": "kubelogin",
                        "args": ["get-token", "--login", login],
                    }
                },
            }
        ],
    }


class Clock:
    def __init__(self) -> None:
        self.now = 10.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Commands:
    """Replace only the process boundary; preparation must create its own config."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[tuple[str, list[str], dict[str, str], float]] = []
        self.failures: dict[str, list[tuple[int, str, str]]] = {}
        self.timeout_stage = ""
        self.cost = 0.1
        self.pods: list[dict[str, Any]] = [
            {
                "metadata": {"name": "api-1"},
                "spec": {"containers": [self.container()]},
                "status": {"phase": "Running"},
            }
        ]
        self.deployment: dict[str, Any] = {
            "spec": {
                "selector": {"matchLabels": {"app": "chaos-app"}},
                "template": {"spec": {"containers": [self.container()]}},
            }
        }
        self.stopped: list[object] = []
        self.temporary_configs: list[Path] = []

    @staticmethod
    def container() -> dict[str, Any]:
        return {
            "name": "app",
            "env": [{"name": name} for name in otel.REQUIRED_OTEL_ENV],
        }

    @staticmethod
    def stage(args: list[str]) -> str:
        for stage in ("get-value", "show", "get-credentials", "convert-kubeconfig"):
            if stage in args:
                return stage
        if "config" in args:
            return "config"
        if "rollout" in args:
            return "rollout-" + next(
                arg for arg in args if arg.startswith("deployment/")
            )
        if "can-i" in args:
            return "can-i"
        if "get" in args:
            return "get-" + args[args.index("get") + 1]
        raise AssertionError(args)

    def respond(
        self, args: list[str], env: dict[str, str], timeout: float
    ) -> tuple[int, str, str]:
        stage = self.stage(args)
        self.calls.append((stage, args, env, timeout))
        if stage == self.timeout_stage:
            self.clock.now += timeout
            raise subprocess.TimeoutExpired(args, timeout, stderr=b"credential-secret")
        self.clock.now += self.cost
        if self.failures.get(stage):
            return self.failures[stage].pop(0)
        name = env["AZURE_ENV_NAME"]
        if stage == "get-value":
            assert args[-2:] == ["-e", name]
            return 0, target_environment(name)[args[args.index("get-value") + 1]], ""
        if stage == "show":
            return (
                0,
                json.dumps(
                    {
                        "id": (
                            f"/subscriptions/subscription-{name}/resourceGroups/rg-{name}"
                            f"/providers/Microsoft.ContainerService/managedClusters/aks-{name}"
                        ),
                        "fqdn": f"{name}.aks.example",
                        "privateFqdn": f"{name}.private.aks.example",
                        "tenantId": "target-tenant",
                    }
                ),
                "",
            )
        if stage == "get-credentials":
            path = Path(args[args.index("--file") + 1])
            assert path.is_absolute() and path.is_file()
            assert not path.read_text(encoding="utf-8")
            assert env["KUBECONFIG"] == str(path)
            path.write_text(
                json.dumps(configuration(name, "azurecli")), encoding="utf-8"
            )
            self.temporary_configs.append(path)
            return 0, "", ""
        if stage == "convert-kubeconfig":
            path = Path(args[args.index("--kubeconfig") + 1])
            assert path in self.temporary_configs
            assert env["KUBECONFIG"] == str(path)
            assert args[args.index("--login") + 1] == "azd"
            assert args[args.index("--tenant-id") + 1] == "target-tenant"
            config = json.loads(path.read_text(encoding="utf-8"))
            assert config["users"][0]["user"]["exec"]["args"][-1] == "azurecli"
            config["users"][0]["user"]["exec"]["args"][-1] = "azd"
            path.write_text(json.dumps(config), encoding="utf-8")
            return 0, "", ""
        path = Path(args[args.index("--kubeconfig") + 1])
        assert path.is_absolute() and path.is_file()
        assert env["KUBECONFIG"] == str(path)
        if stage == "config":
            assert "--raw" not in args
            assert "--minify" in args
            return 0, path.read_text(encoding="utf-8"), ""
        assert args[args.index("--context") + 1] == "same-context-name"
        config = json.loads(path.read_text(encoding="utf-8"))
        assert (
            config["clusters"][0]["cluster"]["server"] == f"https://{name}.aks.example"
        )
        if stage == "get-deployment":
            return 0, json.dumps(self.deployment), ""
        if stage == "get-pod":
            return 0, json.dumps({"items": self.pods}), ""
        if stage == "can-i":
            return 0, "yes", ""
        return 0, "ready", ""

    def popen(self, args: list[str], **popen_kwargs: Any) -> object:
        commands = self

        class Process:
            pid = 987654
            returncode: int | None = None
            stdin = None
            stdout = None
            stderr = None

            def communicate(
                self, *, timeout: float, **kwargs: Any
            ) -> tuple[bytes, bytes]:
                assert kwargs["input"] is None
                code, out, err = commands.respond(args, popen_kwargs["env"], timeout)
                self.returncode = code
                return out.encode(), err.encode()

        return Process()


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Commands:
    clock = Clock()
    result = Commands(clock)
    monkeypatch.setattr(aks.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(aks.time, "sleep", clock.sleep)
    monkeypatch.setattr(aks.shutil, "which", lambda command: command)
    monkeypatch.setattr(aks.subprocess, "Popen", result.popen)
    monkeypatch.setattr(
        aks, "_stop_process", lambda process, job: result.stopped.append(process)
    )
    # Exercise the native Windows launcher separately, not in command-order tests.
    monkeypatch.setattr(aks, "_WindowsJob", lambda: None)
    monkeypatch.setattr(aks, "TEMP_DIRECTORY", tmp_path / "owned")
    for name in (*aks.REQUIRED_ENVIRONMENT, "KUBECONFIG", "AZURE_ENV_NAME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in target_environment().items():
        monkeypatch.setenv(name, value)
    return result


def custom_file(tmp_path: Path, name: str = "a", login: str = "azd") -> Path:
    path = tmp_path / f"{name}-{login}.kubeconfig"
    path.write_text(json.dumps(configuration(name, login)), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "existing_default", [False, True], ids=["C03-empty", "C02-other"]
)
@pytest.mark.parametrize("action", ["require-instrumentation", "check-injected"])
def test_standalone_hooks_prepare_in_order(
    commands: Commands,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    existing_default: bool,
    action: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    default = home / ".kube" / "config"
    if existing_default:
        default.parent.mkdir()
        default.write_text(json.dumps(configuration("other")), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    before = default.read_bytes() if default.exists() else None
    environment = dict(os.environ)

    assert otel.main([action]) == 0

    stages = [call[0] for call in commands.calls]
    assert stages[:4] == ["show", "get-credentials", "convert-kubeconfig", "config"]
    assert stages[4:7] == [
        "get-crd",
        "get-mutatingwebhookconfiguration",
        "get-instrumentation",
    ]
    assert dict(os.environ) == environment
    assert (default.read_bytes() if default.exists() else None) == before
    assert all(not path.exists() for path in commands.temporary_configs)
    for stage, args, env, _ in commands.calls:
        if stage in {"show", "get-credentials"}:
            assert args[args.index("--subscription") + 1] == "subscription-a"
            assert args[args.index("--resource-group") + 1] == "rg-a"
            assert args[args.index("--name") + 1] == "aks-a"
        if stage == "show":
            assert not env.get("KUBECONFIG")


@pytest.mark.parametrize("login", ["azd", "azurecli"])
@pytest.mark.parametrize("cwd", ["", "src/api"], ids=["root", "api"])
@pytest.mark.parametrize("action", ["require-instrumentation", "check-injected"])
def test_custom_authentication_is_preserved(
    commands: Commands,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    login: str,
    cwd: str,
    action: str,
) -> None:
    path = custom_file(tmp_path, login=login)
    before = path.read_bytes()
    monkeypatch.setenv("KUBECONFIG", str(path))
    monkeypatch.chdir(ROOT / cwd)
    assert otel.main([action]) == 0
    assert path.read_bytes() == before
    stages = [call[0] for call in commands.calls]
    assert stages[:2] == ["show", "config"]
    assert "get-credentials" not in stages
    assert "convert-kubeconfig" not in stages
    assert all(env["KUBECONFIG"] == str(path) for _, _, env, _ in commands.calls)


@pytest.mark.parametrize(
    "kind", ["missing", "relative", "malformed", "wrong", "multipath", "empty-context"]
)
def test_invalid_custom_config_stops_before_probes(
    commands: Commands,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    path = custom_file(tmp_path, name="b" if kind == "wrong" else "a")
    if kind == "malformed":
        path.write_text("not valid json", encoding="utf-8")
    elif kind == "empty-context":
        path.write_text('{"current-context": ""}', encoding="utf-8")
    value = str(path)
    if kind == "missing":
        value = str(tmp_path / "missing")
    elif kind == "relative":
        value = "relative.kubeconfig"
    elif kind == "multipath":
        value += os.pathsep + str(tmp_path / "other")
    monkeypatch.setenv("KUBECONFIG", value)
    before = path.read_bytes()
    assert otel.main(["require-instrumentation"]) == 1
    assert path.read_bytes() == before
    assert {call[0] for call in commands.calls} <= {"show", "config"}
    assert not commands.temporary_configs


@pytest.mark.parametrize("case", ["no-environment", "missing-value", "resolve-values"])
def test_environment_resolution_is_explicit(
    commands: Commands, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if case == "no-environment":
        monkeypatch.delenv("AZURE_ENV_NAME")
    else:
        for name in aks.REQUIRED_ENVIRONMENT:
            monkeypatch.delenv(name)
    if case == "missing-value":
        commands.failures["get-value"] = [(0, "", "")]
    assert readiness.main([]) == (0 if case == "resolve-values" else 1)
    if case == "no-environment":
        assert not commands.calls
    elif case == "resolve-values":
        assert [call[0] for call in commands.calls[:3]] == ["get-value"] * 3
        for _, args, _, _ in commands.calls[:3]:
            assert args[-2:] == ["-e", "a"]
    else:
        assert [call[0] for call in commands.calls] == ["get-value"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_instrumentation_precondition_uses_target_state(
    commands: Commands,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    prepared: bool,
    exists: bool,
) -> None:
    if prepared:
        monkeypatch.setenv("KUBECONFIG", str(custom_file(tmp_path)))
    if not exists:
        commands.failures["get-instrumentation"] = [(1, "", "NotFound")] * 5
    assert otel.main(["require-instrumentation", "--timeout-seconds", "2"]) == int(
        not exists
    )
    if not exists:
        assert "Instrumentation" in capsys.readouterr().err
        assert not any(call[0].startswith("rollout") for call in commands.calls)
    assert all(not path.exists() for path in commands.temporary_configs)


@pytest.mark.parametrize(
    ("diagnostic", "category"),
    [
        ("Unauthorized credential-secret", "authentication"),
        ("getting credentials: AADSTS70043 token-secret", "authentication"),
        ("Forbidden credential-secret", "authorization"),
        ("Unable to connect to the server: credential-secret", "connection"),
        ("unexpected credential-secret", "command-failed"),
    ],
)
def test_failures_are_not_reported_as_missing_instrumentation(
    commands: Commands,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    diagnostic: str,
    category: str,
) -> None:
    monkeypatch.setenv("KUBECONFIG", str(custom_file(tmp_path, login="azurecli")))
    commands.failures["get-crd"] = [(1, "", diagnostic)]
    assert otel.main(["require-instrumentation"]) == 1
    error = capsys.readouterr().err
    assert category in error
    assert "secret" not in error
    assert "Missing:" not in error


@pytest.mark.parametrize(
    "failure", [(1, "no", ""), (1, "", "Forbidden"), (1, "", "Unable to connect")]
)
def test_readiness_recovers_without_changing_target(
    commands: Commands, failure: tuple[int, str, str]
) -> None:
    commands.failures["can-i"] = [failure]
    assert readiness.main(["--poll-seconds", "1"]) == 0
    assert commands.clock.sleeps == [1]
    assert [call[0] for call in commands.calls].count("get-credentials") == 1


def test_temporary_absence_recovers(commands: Commands) -> None:
    commands.failures["get-instrumentation"] = [(1, "", "NotFound")]
    assert otel.main(["require-instrumentation", "--poll-seconds", "1"]) == 0
    assert commands.clock.sleeps == [1]
    assert [call[0] for call in commands.calls].count("get-instrumentation") == 2


def test_readiness_authentication_failure_is_not_an_rbac_wait(
    commands: Commands, capsys: pytest.CaptureFixture[str]
) -> None:
    commands.failures["get-namespace"] = [(1, "", "Unauthorized credential-secret")]
    assert readiness.main([]) == 1
    assert not commands.clock.sleeps
    error = capsys.readouterr().err
    assert "authentication" in error
    assert "waiting for AKS readiness" not in error
    assert "secret" not in error


@pytest.mark.parametrize(
    "stage",
    [
        "get-value",
        "show",
        "get-credentials",
        "convert-kubeconfig",
        "config",
        "get-crd",
        "rollout-deployment/app-monitoring-webhook",
        "rollout-deployment/chaos-app",
        "get-deployment",
        "get-pod",
    ],
)
def test_whole_hook_deadline_covers_every_stage(
    commands: Commands,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    if stage == "get-value":
        monkeypatch.delenv("AZURE_RESOURCE_GROUP")
    commands.timeout_stage = stage
    assert otel.main(["check-injected", "--timeout-seconds", "10"]) == 1
    assert commands.calls[-1][0] == stage
    assert len(commands.stopped) == len(commands.calls)
    assert sum(commands.clock.sleeps) == 0
    error = capsys.readouterr().err
    assert "deadline" in error
    assert "secret" not in error
    assert all(not path.exists() for path in commands.temporary_configs)
    budgets = [call[3] for call in commands.calls]
    assert budgets == sorted(budgets, reverse=True)
    assert budgets[-1] <= budgets[0] <= 10


def test_rollout_and_sleep_do_not_extend_budget(commands: Commands) -> None:
    assert otel.main(["check-injected", "--timeout-seconds", "3"]) == 0
    for stage, args, _, timeout in commands.calls:
        if stage.startswith("rollout"):
            rollout_timeout = next(arg for arg in args if arg.startswith("--timeout="))
            assert float(
                rollout_timeout.removeprefix("--timeout=").removesuffix("s")
            ) <= (timeout + 0.001)
    commands.calls.clear()
    commands.failures["get-crd"] = [(1, "", "NotFound")]
    assert (
        otel.main(
            [
                "require-instrumentation",
                "--timeout-seconds",
                "2",
                "--poll-seconds",
                "60",
            ]
        )
        == 1
    )
    assert len(commands.clock.sleeps) == 1
    assert 0 < commands.clock.sleeps[0] < 2
    assert commands.calls[-1][0] == "get-instrumentation"


def test_near_deadline_response_cannot_start_next_operation(commands: Commands) -> None:
    commands.cost = 1
    assert otel.main(["check-injected", "--timeout-seconds", "4"]) == 1
    assert [call[0] for call in commands.calls] == [
        "show",
        "get-credentials",
        "convert-kubeconfig",
        "config",
    ]


@pytest.mark.parametrize("custom", [False, True])
def test_a_b_a_does_not_reuse_connection_state(
    commands: Commands, tmp_path: Path, custom: bool
) -> None:
    paths = {name: custom_file(tmp_path, name) for name in ("a", "b")}
    before = {name: path.read_bytes() for name, path in paths.items()}
    environments = []
    for name in ("a", "b", "a"):
        environment = target_environment(name)
        if custom:
            environment["KUBECONFIG"] = str(paths[name])
        environments.append(dict(environment))
        with aks.aks_connection(environ=environment) as connection:
            assert connection.probe(["get", "namespace", "default", "-o", "name"])
            assert connection.environment == environment
        assert environment == environments[-1]
    assert {name: path.read_bytes() for name, path in paths.items()} == before
    configs = [
        args[args.index("--kubeconfig") + 1]
        for stage, args, _, _ in commands.calls
        if stage == "config"
    ]
    if custom:
        assert configs == [str(paths[name]) for name in ("a", "b", "a")]
    else:
        assert len(set(configs)) == 3
        assert all(not Path(path).exists() for path in configs)


@pytest.mark.parametrize("kind", ["no-pods", "wrong-container", "missing-env"])
def test_injection_validation_does_not_accept_false_success(
    commands: Commands, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    if kind == "no-pods":
        commands.pods = []
    elif kind == "wrong-container":
        commands.deployment["spec"]["template"]["spec"]["containers"][0]["name"] = (
            "sidecar"
        )
    else:
        commands.pods[0]["spec"]["containers"][0]["env"] = []
    assert otel.main(["check-injected"]) == 1
    assert "API deployment and pods contain" not in capsys.readouterr().out


def test_empty_probe_is_not_success(commands: Commands) -> None:
    commands.failures["get-crd"] = [(0, "", "")]
    assert otel.main(["require-instrumentation"]) == 1


@pytest.mark.parametrize(
    "server",
    [
        "https://b.aks.example",
        "http://a.aks.example",
        "https://a.aks.example:444",
        "https://a.aks.example/path",
    ],
)
def test_context_name_is_not_target_identity(server: str) -> None:
    config = configuration()
    config["clusters"][0]["cluster"]["server"] = server
    with pytest.raises(aks.AKSConnectionError, match="does not match"):
        aks._selected_context(config, {"a.aks.example"})


def test_private_endpoint_is_supported() -> None:
    config = configuration()
    config["clusters"][0]["cluster"]["server"] = "https://A.PRIVATE.AKS.EXAMPLE.:443/"
    assert (
        aks._selected_context(config, {"a.private.aks.example"}) == "same-context-name"
    )


@pytest.mark.parametrize("missing", ["id", "fqdn"])
def test_incomplete_arm_target_stops(commands: Commands, missing: str) -> None:
    target: dict[str, str] = {
        "id": "/subscriptions/subscription-a/resourceGroups/rg-a/providers/Microsoft.ContainerService/managedClusters/aks-a",
        "fqdn": "a.aks.example",
    }
    target.pop(missing)
    commands.failures["show"] = [(0, json.dumps(target), "")]
    assert readiness.main([]) == 1
    assert [call[0] for call in commands.calls] == ["show"]


def test_deadline_starts_before_environment_lookup(
    commands: Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_RESOURCE_GROUP")
    commands.cost = 1
    assert readiness.main(["--timeout-seconds", "1"]) == 1
    assert [call[0] for call in commands.calls] == ["get-value"]


def test_stop_process_tree_has_bounded_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    waited: list[float] = []

    class Process:
        pid = 123456

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            killed.append(self.pid)

        def wait(self, timeout: float) -> None:
            waited.append(timeout)
            raise subprocess.TimeoutExpired("owned", timeout)

    class Job:
        def close(self) -> None:
            killed.append(654321)

    aks._stop_process(Process(), Job())
    assert killed == [654321, 123456]
    assert waited == [aks.STOP_TIMEOUT_SECONDS]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group contract")
def test_posix_kills_descendants_even_if_root_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[int] = []

    class Process:
        pid = 123456

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float) -> int:
            assert timeout == aks.STOP_TIMEOUT_SECONDS
            return 0

    monkeypatch.setattr(aks.os, "killpg", lambda pid, signal: killed.append(pid))
    aks._stop_process(Process(), None)
    assert killed == [123456]


def test_windows_gate_is_released_only_after_job_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Job:
        def assign(self, pid: int) -> None:
            assert pid == 123456
            events.append("assign")

        def close(self) -> None:
            events.append("close-job")

    class Process:
        pid = 123456
        stdin = None
        stdout = None
        stderr = None

        def communicate(self, *, timeout: float, **kwargs: Any) -> tuple[bytes, bytes]:
            assert events == ["popen", "assign"]
            assert kwargs["input"] == b"\n"
            assert 0 < timeout <= 10
            events.append("release-gate")
            raise subprocess.TimeoutExpired("owned", timeout)

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            events.append("kill-root")

        def wait(self, timeout: float) -> None:
            assert timeout == aks.STOP_TIMEOUT_SECONDS
            events.append("wait")

    def popen(args: list[str], **kwargs: Any) -> Process:
        assert args[:2] == [sys.executable, "-c"]
        assert "sys.stdin.buffer.read(1)" in args[2]
        assert args[3:] == ["kubectl", "version"]
        assert kwargs["start_new_session"] is False
        events.append("popen")
        return Process()

    monkeypatch.setattr(aks, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(aks, "_WindowsJob", Job)
    monkeypatch.setattr(aks.shutil, "which", lambda command: command)
    monkeypatch.setattr(aks.subprocess, "Popen", popen)
    with pytest.raises(aks.DeadlineExceeded):
        aks.run_command(
            ["kubectl", "version"],
            deadline=aks.Deadline(10),
            env={},
            operation="owned Windows command",
        )
    assert events == [
        "popen",
        "assign",
        "release-gate",
        "close-job",
        "kill-root",
        "wait",
        "close-job",
    ]


def test_real_authentication_descendant_is_stopped(tmp_path: Path) -> None:
    started = tmp_path / "started"
    escaped = tmp_path / "escaped"
    child = (
        "import pathlib,time;"
        f"pathlib.Path({str(started)!r}).write_text('started');"
        "time.sleep(1.5);"
        f"pathlib.Path({str(escaped)!r}).write_text('escaped')"
    )
    parent = (
        "import subprocess,sys;"
        f"subprocess.Popen([sys.executable, '-B', '-c', {child!r}]);"
        "sys.exit(0)"
    )
    begin = time.monotonic()
    with pytest.raises(aks.DeadlineExceeded):
        aks.run_command(
            [sys.executable, "-B", "-c", parent],
            deadline=aks.Deadline(1),
            env=dict(os.environ),
            operation="owned authentication descendant test",
        )
    assert time.monotonic() - begin < 1 + aks.STOP_TIMEOUT_SECONDS + 2
    assert started.exists()
    time.sleep(0.8)
    assert not escaped.exists()


@pytest.mark.parametrize("custom", [False, True])
def test_a_b_a_in_separate_processes(tmp_path: Path, custom: bool) -> None:
    paths = {name: custom_file(tmp_path, name) for name in ("a", "b")}
    before = {name: path.read_bytes() for name, path in paths.items()}
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import test_aks_connection as tests
aks = tests.aks
clock = tests.Clock()
commands = tests.Commands(clock)
aks.time.monotonic = clock.monotonic
aks.time.sleep = clock.sleep
aks.shutil.which = lambda command: command
aks.subprocess.Popen = commands.popen
aks._stop_process = lambda process, job: None
aks._WindowsJob = lambda: None
aks.TEMP_DIRECTORY = Path(sys.argv[2]) / "owned"
env = tests.target_environment(sys.argv[3])
if sys.argv[4]:
    env["KUBECONFIG"] = sys.argv[4]
with aks.aks_connection(environ=env) as connection:
    assert connection.probe(["get", "namespace", "default", "-o", "name"])
    result = {"path": str(connection.kubeconfig), "env": connection.environment}
result["stages"] = [call[0] for call in commands.calls]
print(json.dumps(result))
"""
    results = []
    for name in ("a", "b", "a"):
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(ROOT / "scripts" / "tests"),
                str(tmp_path),
                name,
                str(paths[name]) if custom else "",
            ],
            cwd=ROOT if name == "a" else ROOT / "src" / "api",
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        result = json.loads(completed.stdout)
        assert result["env"]["AZURE_ENV_NAME"] == name
        expected = ["show", "config", "get-namespace"]
        if not custom:
            expected[1:1] = ["get-credentials", "convert-kubeconfig"]
        assert result["stages"] == expected
        results.append(result)
    assert {name: path.read_bytes() for name, path in paths.items()} == before
    if custom:
        assert [result["path"] for result in results] == [
            str(paths[name]) for name in ("a", "b", "a")
        ]
    else:
        assert len({result["path"] for result in results}) == 3
        assert all(not Path(result["path"]).exists() for result in results)
        assert all("KUBECONFIG" not in result["env"] for result in results)
