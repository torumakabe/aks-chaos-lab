# ruff: noqa: S606, S607 -- PATH で CLI を差し替え、Bash をテスト用 tty に接続する。

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Bash and POSIX tty tests")
SCRIPT = Path(__file__).resolve().parents[1] / "import-sre-agent-github-key.sh"
PROMPT = "GitHub App の秘密鍵（PEM 形式）".encode()
INPUT_PROMPT_END = "（入力は非表示、Ctrl+C で中止）:\r\n".encode()

MOCK = r"""#!/usr/bin/env bash
set -euo pipefail
[ -z "${PEM-}" ] && [ -z "${LINE-}" ] || { echo "Secret exported" >&2; exit 99; }
event() { printf '%s\n' "$1" >> "$STATE/events"; }
case "${0##*/}" in
azd)
  case "$3" in
    AZURE_SUBSCRIPTION_ID) echo sub;;
    AZURE_RESOURCE_GROUP) echo rg;;
    AZURE_AKS_CLUSTER_NAME) echo aks;;
    AZURE_SRE_AGENT_VAULT_NAME) echo vault;;
    *) exit 90;;
  esac;;
kubelogin) :;;
az)
  case "$1 $2 ${3-}" in
    "login --service-principal "*)
      if [ "${FAIL_LOGIN_ONCE-}" = 1 ] && [ ! -f "$STATE/login-attempted" ]; then
        touch "$STATE/login-attempted"
        echo "AADSTS70025: az logout; az login" >&2
        exit 1
      fi;;
    "aks get-credentials "*)
      while [ "$1" != "--file" ]; do shift; done
      touch "$2";;
    "account show "*) echo tenant;;
    "aks show "*) echo issuer;;
    "identity list "*)
      if [ -f "$STATE/identity" ]; then
        case "$*" in
          *principalId*) echo principal;;
          *) cat "$STATE/identity";;
        esac
      fi;;
    "identity create "*)
      event identity-create
      echo aks-chaos-lab-github-import > "$STATE/identity";;
    "identity show "*)
      case "$*" in *clientId*) echo client;; *) echo principal;; esac;;
    "identity delete "*)
      event identity-delete
      rm -f "$STATE/identity";;
    "identity federated-credential create") :;;
    "role assignment create")
      case " $* " in
        *" --role 14b46e9e-c2b7-41b4-b07b-48a6ebf60603 "*) :;;
        *) echo "Expected Key Vault Crypto Officer role ID" >&2; exit 94;;
      esac
      touch "$STATE/role"
      event role-create
      [ "${FAIL_ROLE_RESPONSE-}" != 1 ];;
    "role assignment list")
      if [ -f "$STATE/role" ]; then echo role-id; fi;;
    "role assignment delete")
      event role-delete
      case "$*" in
        *legacy-role-id*) rm -f "$STATE/legacy-role";;
        *) rm -f "$STATE/role";;
      esac;;
    "keyvault key list")
      if [ "${FAIL_PREFLIGHT-}" = 1 ]; then
        echo "ForbiddenByRbac: final diagnostic" >&2
        exit 1
      fi
      if [ "${FAIL_VAULT_ONCE-}" = 1 ] && [ ! -f "$STATE/vault-attempted" ]; then
        touch "$STATE/vault-attempted"
        echo "ForbiddenByRbac: transient diagnostic" >&2
        exit 1
      fi
      if [ -f "$STATE/public" ]; then echo sre-agent-github-app-key; fi;;
    "keyvault key download")
      while [ "$1" != "--file" ]; do shift; done
      cp "$STATE/public" "$2";;
    "keyvault key import")
      event key-import
      while [ "$1" != "--pem-file" ]; do shift; done
      openssl rsa -in "$2" -pubout > "$STATE/public" 2>/dev/null
      [ "${FAIL_IMPORT_RESPONSE-}" != 1 ];;
    "keyvault key show")
      case "$*" in
        *key.kty*) printf 'RSA\tTrue\tTrue\tTrue\n';;
        *) echo https://vault.vault.azure.net/keys/sre-agent-github-app-key/version;;
      esac;;
    *) echo "Unexpected az command: $*" >&2; exit 91;;
  esac;;
kubectl)
  if [ "$1" = "-n" ]; then shift 2; fi
  case "$1 $2" in
    "get namespace")
      if [ "$3" = default ]; then
        echo namespace/default
      elif [ -f "$STATE/namespace" ]; then
        printf 'sre-github-key-import:%s' "$(cat "$STATE/namespace")"
      fi;;
    "delete namespace")
      event namespace-delete
      [ "${FAIL_CLEANUP-}" != 1 ] || exit 1
      rm -f "$STATE/namespace"
      rm -f "$STATE/key/app.pem" "$STATE/key/existing.pem";;
    "apply -f")
      cat >/dev/null
      echo aks-chaos-lab-github-import > "$STATE/namespace"
      event namespace-create;;
    "wait --for=condition=Ready")
      event pod-ready
      [ "${FAIL_READY-}" != 1 ];;
    "exec "*)
      [ -f "$STATE/namespace" ] || exit 1
      while [ "$1" != "--" ]; do shift; done
      shift 3
      body=$1
      shift
      body=${body//\/key\//$STATE/key/}
      body=${body//\/token\/jwt/$STATE/token}
      exec /bin/sh -ceu "$body" "$@";;
    *) echo "Unexpected kubectl command: $*" >&2; exit 92;;
  esac;;
sleep) :;;
*) exit 93;;
esac
"""


@pytest.fixture
def cli(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    assert shutil.which("bash") and shutil.which("openssl")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("az", "azd", "kubectl", "kubelogin", "sleep"):
        path = bin_dir / tool
        path.write_text(MOCK)
        path.chmod(0o700)
    state = tmp_path / "state"
    state.mkdir()
    (state / "key").mkdir()
    (state / "token").write_text("dummy-federated-token")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STATE": str(state),
        "PEM": "inherited-variable",
        "LINE": "inherited-variable",
    }
    return state, env


def run(
    env: dict[str, str], *, cleanup_only: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "test", *(["--cleanup-only"] if cleanup_only else [])],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def events(state: Path) -> list[str]:
    path = state / "events"
    return path.read_text().splitlines() if path.exists() else []


def assert_clean(state: Path) -> None:
    for name in ("identity", "role", "namespace", "key/app.pem", "key/existing.pem"):
        assert not (state / name).exists(), name


@pytest.mark.parametrize(
    "failure", ["FAIL_READY", "FAIL_PREFLIGHT", "FAIL_ROLE_RESPONSE"]
)
def test_failure_before_input_cleans_and_can_restart(
    cli: tuple[Path, dict[str, str]], failure: str
) -> None:
    state, env = cli
    result = run({**env, failure: "1"})
    assert result.returncode != 0
    assert PROMPT.decode() not in result.stdout + result.stderr
    assert_clean(state)
    assert "key-import" not in events(state)
    assert run(env, cleanup_only=True).returncode == 0


def test_cleanup_retries_leftovers_and_preserves_registered_key(
    cli: tuple[Path, dict[str, str]],
) -> None:
    state, env = cli
    (state / "namespace").write_text("aks-chaos-lab-github-import")
    (state / "identity").write_text("aks-chaos-lab-github-import")
    (state / "role").touch()
    (state / "public").write_text("registered-public-key")
    result = run({**env, "FAIL_CLEANUP": "1"}, cleanup_only=True)
    assert result.returncode != 0
    assert (state / "identity").exists()
    assert run(env, cleanup_only=True).returncode == 0
    assert_clean(state)
    assert (state / "public").read_text() == "registered-public-key"
    assert events(state)[-3:] == ["namespace-delete", "role-delete", "identity-delete"]


@pytest.mark.parametrize("resource", ["namespace", "identity"])
def test_cleanup_refuses_unowned_resources(
    cli: tuple[Path, dict[str, str]], resource: str
) -> None:
    state, env = cli
    (state / resource).write_text("someone-else")
    result = run(env, cleanup_only=True)
    assert result.returncode != 0
    assert (state / resource).exists()
    assert "identity-create" not in events(state)


def run_with_tty(
    env: dict[str, str],
    secret: bytes,
) -> tuple[int, bytes]:
    import pty

    pid, master = pty.fork()
    if pid == 0:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.tcsetpgrp(0, os.getpgrp())
        os.execvpe("bash", ["bash", str(SCRIPT), "test"], env)
    output = b""
    sent = False
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            # 複数行の案内は分割して届くため、最初の改行では入力を開始しない。
            if not sent and INPUT_PROMPT_END in output:
                os.write(master, secret)
                sent = True
        else:
            os.kill(pid, 9)
            pytest.fail(f"Timed out waiting for the import command: {output!r}")
    finally:
        os.close(master)
        _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), output


@pytest.fixture
def secret(tmp_path: Path) -> bytes:
    # テスト専用に生成し、GitHub App の実際の秘密鍵は使わない。
    result = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_hidden_input_and_uncertain_import_can_restart_without_new_version(
    cli: tuple[Path, dict[str, str]], secret: bytes
) -> None:
    state, env = cli
    rc, output = run_with_tty({**env, "FAIL_IMPORT_RESPONSE": "1"}, secret)
    assert rc != 0
    assert output.count(PROMPT) == 1
    assert secret.splitlines()[1] not in output
    assert_clean(state)
    assert (state / "public").exists(), output.decode(errors="replace")
    rc, output = run_with_tty(
        {**env, "FAIL_LOGIN_ONCE": "1", "FAIL_VAULT_ONCE": "1"}, secret
    )
    assert rc == 0, output.decode(errors="replace")
    assert output.count(PROMPT) == 1
    assert secret.splitlines()[1] not in output
    assert events(state).count("key-import") == 1
    assert_clean(state)


def test_invalid_secret_is_removed(
    cli: tuple[Path, dict[str, str]],
) -> None:
    state, env = cli
    rc, _ = run_with_tty(env, b"invalid\n-----END PRIVATE KEY-----\n")
    assert rc != 0
    assert "key-import" not in events(state)
    assert_clean(state)


def test_different_key_is_not_overwritten(
    cli: tuple[Path, dict[str, str]], secret: bytes
) -> None:
    state, env = cli
    rc, output = run_with_tty(env, secret)
    assert rc == 0, output.decode(errors="replace")
    original = (state / "public").read_bytes()
    other = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        check=True,
    ).stdout
    assert run_with_tty(env, other)[0] != 0
    assert (state / "public").read_bytes() == original
    assert events(state).count("key-import") == 1
    assert_clean(state)
