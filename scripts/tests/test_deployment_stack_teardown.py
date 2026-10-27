from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "validate-deployment-stacks-before-down.py"
)
ENV_NAME = "inttest-123"
SUBSCRIPTION_ID = "selected-subscription"
RESOURCE_GROUP = f"rg-aks-chaos-lab-{ENV_NAME}"
SERVICE_GROUP_ID = (
    f"/providers/Microsoft.Management/serviceGroups/sg-aks-chaos-lab-{ENV_NAME}-abc123"
)
SLI_ID = f"{SERVICE_GROUP_ID}/providers/Microsoft.Monitor/slis/availability"


@pytest.fixture
def guard(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("stack_down_guard", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "require_command", lambda _command: None)
    monkeypatch.setattr(
        module,
        "get_env_value",
        lambda name: {
            "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION_ID,
            "AZURE_ENV_NAME": ENV_NAME,
            "AZURE_RESOURCE_GROUP": RESOURCE_GROUP,
        }.get(name, ""),
    )
    return module


def stack_entry(layer: str, *, resource_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": f"azd-stack-{ENV_NAME}-{layer}",
        "tags": {"azd-env-name": ENV_NAME, "azd-layer-name": layer},
        "resources": [{"id": resource_id} for resource_id in (resource_ids or [])],
    }


def configure(
    guard: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    stacks: list[dict[str, Any]],
    *,
    enabled: bool = True,
    resource_group_exists: bool = False,
) -> None:
    monkeypatch.setattr(guard, "stacks_enabled", lambda: enabled)
    monkeypatch.setattr(guard, "command_json", lambda _args: stacks)
    monkeypatch.setattr(
        guard,
        "resource_group_exists",
        lambda _resource_group, _subscription: resource_group_exists,
    )
    monkeypatch.setattr(guard, "service_group_exists", lambda *_args: True)
    monkeypatch.setattr(guard, "service_group_has_slis", lambda *_args: False)


def test_stacks_must_be_enabled(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(guard, monkeypatch, [], enabled=False)
    with pytest.raises(SystemExit):
        guard.main()


def test_stackless_environment_with_resource_group_must_be_imported(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(guard, monkeypatch, [], resource_group_exists=True)
    with pytest.raises(SystemExit):
        guard.main()


def test_empty_environment_can_be_deleted_with_stacks_enabled(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(guard, monkeypatch, [])
    assert guard.main() == 0


def test_base_and_sli_stacks_pass_validation(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(guard, monkeypatch, [stack_entry("base"), stack_entry("sli")])
    assert guard.main() == 0


def test_stack_tags_must_match_selected_environment(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = stack_entry("base")
    stack["tags"]["azd-env-name"] = "other"
    configure(guard, monkeypatch, [stack])
    with pytest.raises(SystemExit):
        guard.main()


def test_sli_stack_without_base_stack_blocks_deletion(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(guard, monkeypatch, [stack_entry("sli")])
    with pytest.raises(SystemExit):
        guard.main()


def test_base_stack_with_unmanaged_slis_blocks_deletion(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(
        guard,
        monkeypatch,
        [stack_entry("base", resource_ids=[SERVICE_GROUP_ID])],
    )
    monkeypatch.setattr(guard, "service_group_has_slis", lambda *_args: True)
    with pytest.raises(SystemExit):
        guard.main()


def test_base_stack_without_slis_can_be_deleted_without_sli_stack(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(
        guard,
        monkeypatch,
        [stack_entry("base", resource_ids=[SERVICE_GROUP_ID])],
    )
    assert guard.main() == 0


def test_missing_service_group_is_not_treated_as_an_api_failure(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guard,
        "run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, "", 'ERROR: Not Found({"error":{"code":"ResourceNotFound"}})'
        ),
    )
    assert guard.service_group_exists(SERVICE_GROUP_ID, SUBSCRIPTION_ID) is False


def test_service_group_api_failure_stops_teardown(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guard,
        "run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, "", "ERROR: AuthorizationFailed"
        ),
    )
    with pytest.raises(SystemExit):
        guard.service_group_exists(SERVICE_GROUP_ID, SUBSCRIPTION_ID)


def test_service_group_response_must_match_requested_id(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guard,
        "run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({"id": SERVICE_GROUP_ID + "-other"}), ""
        ),
    )
    with pytest.raises(SystemExit):
        guard.service_group_exists(SERVICE_GROUP_ID, SUBSCRIPTION_ID)


def test_service_group_sli_query_confirms_owned_sli(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guard, "command_json", lambda _args: {"value": [{"id": SLI_ID}]}
    )
    assert guard.service_group_has_slis(SERVICE_GROUP_ID, SUBSCRIPTION_ID) is True


def test_service_group_sli_query_rejects_foreign_sli(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guard,
        "command_json",
        lambda _args: {"value": [{"id": SERVICE_GROUP_ID + "/other"}]},
    )
    with pytest.raises(SystemExit):
        guard.service_group_has_slis(SERVICE_GROUP_ID, SUBSCRIPTION_ID)
