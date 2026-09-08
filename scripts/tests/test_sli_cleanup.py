from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from email.message import Message
from http.client import HTTPException
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "cleanup-azure-monitor-sli-resources.py"
ENV_NAME = "inttest-123"
SUBSCRIPTION = "selected-subscription"
RESOURCE_GROUP = f"rg-aks-chaos-lab-{ENV_NAME}"
CLUSTER_NAME = f"aks-aks-chaos-lab-{ENV_NAME}"
CLUSTER_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.ContainerService/managedClusters/{CLUSTER_NAME}"
)
SERVICE_GROUP = f"sg-aks-chaos-lab-{ENV_NAME}-abc123"
SERVICE_GROUP_ID = f"/providers/Microsoft.Management/serviceGroups/{SERVICE_GROUP}"
SLI_ID = f"{SERVICE_GROUP_ID}/providers/Microsoft.Monitor/slis/availability"
DCRA_ID = (
    f"{CLUSTER_ID}/providers/Microsoft.Insights/"
    "dataCollectionRuleAssociations/OtlpAppInsightsExtension"
)
DCRA_LIST_URL = f"https://management.azure.com{DCRA_ID.rsplit('/', 1)[0]}"
SERVICE_GROUP_URL = f"https://management.azure.com{SERVICE_GROUP_ID}"
SLI_LIST_URL = (
    f"https://management.azure.com{SERVICE_GROUP_ID}/providers/Microsoft.Monitor/slis"
)
SERVICE_GROUP_NOT_FOUND = (
    'ERROR: Not Found({"error":{"code":"ResourceNotFound",'
    '"message":"The group cannot be found."}})\n'
)
OPERATION_URL = (
    "https://management.azure.com/providers/Microsoft.Management/"
    "operationResults/test-operation?api-version=2024-02-01-preview"
)


class HttpResponse(io.BytesIO):
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        super().__init__(b"")
        self.status = status
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value


class AzureCli:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.default_subscription = "unrelated-default-subscription"
        self.selected_subscriptions: list[str] = []
        self.azd_result = subprocess.CompletedProcess([], 1, "", "")
        self.cluster_id = CLUSTER_ID
        self.service_groups = [{"name": SERVICE_GROUP, "id": SERVICE_GROUP_ID}]
        self.sli_ids = [SLI_ID]
        self.deleted_rg = False
        self.deleted_dcra = False
        self.deleted_service_groups: set[str] = set()
        self.deleted_slis: set[str] = set()
        self.deleted_deployments: set[str] = set()
        self.failure: tuple[str, ...] = ()
        self.responses: dict[str, list[tuple[int, str, str]]] = {}
        self.http_responses: list[
            tuple[int, dict[str, str]] | OSError | HTTPException
        ] = []
        self.http_requests: list[urllib.request.Request] = []
        self.http_timeouts: list[float] = []
        self.pending_service_groups: set[str] = set()
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(
        self, args: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[0] == "azd":
            return self.azd_result
        assert args[0] == "az"
        selected = (
            args[args.index("--subscription") + 1]
            if "--subscription" in args
            else self.default_subscription
        )
        self.selected_subscriptions.append(selected)
        if self.failure and args[1 : 1 + len(self.failure)] == list(self.failure):
            return subprocess.CompletedProcess(args, 1, "", "simulated Azure error")
        operation = " ".join(args[1:3])
        if args[1] == "rest":
            method = args[args.index("--method") + 1]
            url = args[args.index("--url") + 1]
            operation = f"{method} {url.partition('?')[0]}"
        responses = self.responses.get(operation)
        if responses:
            returncode, stdout, stderr = responses.pop(0)
            return subprocess.CompletedProcess(args, returncode, stdout, stderr)
        payload: Any
        if args[1:3] == ["account", "get-access-token"]:
            return subprocess.CompletedProcess(args, 0, "fake-access-value", "")
        if args[1:3] == ["resource", "list"]:
            assert args[args.index("--resource-group") + 1] == RESOURCE_GROUP
            assert args[args.index("--name") + 1] == CLUSTER_NAME
            assert (
                args[args.index("--resource-type") + 1]
                == "Microsoft.ContainerService/managedClusters"
            )
            payload = [{"id": self.cluster_id}]
        elif args[1:4] == ["deployment", "sub", "list"]:
            payload = [
                {
                    "name": f"{env}-{layer}",
                    "tags": {"azd-env-name": env, "azd-layer-name": layer},
                }
                for env in (ENV_NAME, "other")
                for layer in ("sli", "base", "other-layer")
                if f"{env}-{layer}" not in self.deleted_deployments
            ]
        elif args[1:5] == ["deployment", "operation", "sub", "list"]:
            assert args[args.index("--name") + 1] == f"{ENV_NAME}-base"
            assert args[args.index("--query") + 1] == (
                "[?properties.targetResource.resourceType=='Microsoft.Management/serviceGroups']"
                ".properties.targetResource.id"
            )
            payload = [item["id"] for item in self.service_groups]
        elif args[1:3] == ["group", "exists"]:
            return subprocess.CompletedProcess(
                args, 0, "false" if self.deleted_rg else "true", ""
            )
        elif args[1:3] == ["group", "delete"]:
            self.deleted_rg = True
            payload = None
        elif args[1] == "rest":
            method = args[args.index("--method") + 1]
            url = args[args.index("--url") + 1]
            if method == "delete":
                resource_id = url.removeprefix(
                    "https://management.azure.com"
                ).partition("?")[0]
                if "OtlpAppInsightsExtension" in url:
                    self.deleted_dcra = True
                elif "/slis/" in url:
                    self.deleted_slis.add(resource_id)
                elif "/serviceGroups/" in url:
                    raise AssertionError(
                        "SG DELETE must wait for its Location operation"
                    )
                payload = None
            elif "/slis?" in url:
                payload = {
                    "value": [
                        {"id": item}
                        for item in self.sli_ids
                        if item not in self.deleted_slis
                    ]
                }
            elif "/dataCollectionRuleAssociations?" in url:
                payload = {"value": [] if self.deleted_dcra else [{"id": DCRA_ID}]}
            elif "/serviceGroups/" in url:
                resource_id = url.removeprefix(
                    "https://management.azure.com"
                ).partition("?")[0]
                if resource_id in self.deleted_service_groups:
                    return subprocess.CompletedProcess(
                        args, 1, "", SERVICE_GROUP_NOT_FOUND
                    )
                payload = {"id": resource_id}
            else:
                raise AssertionError(f"unexpected REST request: {args}")
        elif args[1:4] == ["deployment", "sub", "delete"]:
            self.deleted_deployments.add(args[args.index("--name") + 1])
            payload = None
        else:
            raise AssertionError(f"unexpected command: {args}")
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    def open(self, request: urllib.request.Request, timeout: float) -> HttpResponse:
        method = request.get_method()
        self.http_requests.append(request)
        self.http_timeouts.append(timeout)
        self.calls.append(
            ["http", "--method", method.lower(), "--url", request.full_url]
        )
        assert request.get_header("Authorization") == "Bearer fake-access-value"
        if self.http_responses:
            response = self.http_responses.pop(0)
            if isinstance(response, (OSError, HTTPException)):
                raise response
            status, headers = response
        elif method == "DELETE":
            status, headers = 202, {"Location": OPERATION_URL}
        else:
            status, headers = 204, {}
        if status >= 300:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "simulated HTTP error",
                Message(),
                io.BytesIO(),
            )
        if method == "DELETE":
            resource_id = request.full_url.removeprefix(
                "https://management.azure.com"
            ).partition("?")[0]
            if status == 202:
                self.pending_service_groups.add(resource_id)
            elif status == 204:
                self.deleted_service_groups.add(resource_id)
        elif status in {200, 204}:
            self.deleted_service_groups.update(self.pending_service_groups)
            self.pending_service_groups.clear()
        return HttpResponse(status, headers)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    @property
    def deletes(self) -> list[list[str]]:
        return [args for args in self.calls if "delete" in args]


@pytest.fixture
def cleanup(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("sli_cleanup", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in os.environ:
        if name.startswith(("AZURE_", "CONFIRM_DELETE_AZURE_MONITOR")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AZURE_ENV_NAME", ENV_NAME)
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", SUBSCRIPTION)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    monkeypatch.setattr(
        module.shutil, "which", lambda name: name if name == "az" else None
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return module


@pytest.fixture
def cli(cleanup: ModuleType, monkeypatch: pytest.MonkeyPatch) -> AzureCli:
    fake = AzureCli()
    monkeypatch.setattr(cleanup.subprocess, "run", fake)
    monkeypatch.setattr(cleanup.time, "monotonic", lambda: fake.now)
    monkeypatch.setattr(cleanup.time, "sleep", fake.sleep)
    build_opener = urllib.request.build_opener

    def build(
        handler: urllib.request.HTTPRedirectHandler,
    ) -> urllib.request.OpenerDirector:
        assert isinstance(handler, cleanup.NoRedirectHandler)
        return build_opener(handler)

    monkeypatch.setattr(urllib.request, "build_opener", build)

    def open_response(
        opener: urllib.request.OpenerDirector,
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> HttpResponse:
        return fake.open(request, timeout)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", open_response)
    return fake


@pytest.mark.parametrize("value", [None, "", "  ", "ERROR: key not found"])
def test_missing_subscription_never_issues_deletes(
    cleanup: ModuleType,
    cli: AzureCli,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID")
    else:
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", value)
    assert cleanup.main() == 1
    assert cli.calls == []
    assert "AZURE_SUBSCRIPTION_ID is required" in capsys.readouterr().err


def test_service_group_location_completes_before_dcra_and_record_deletion(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.http_responses = [
        (202, {"Location": OPERATION_URL, "Retry-After": "2"}),
        (202, {"Retry-After": "3"}),
        (200, {}),
    ]
    assert cleanup.main() == 0
    assert [request.get_method() for request in cli.http_requests] == [
        "DELETE",
        "GET",
        "GET",
    ]
    assert all(request.full_url == OPERATION_URL for request in cli.http_requests[1:])
    assert cli.sleeps == [2, 3]
    last_poll = max(index for index, args in enumerate(cli.calls) if args[0] == "http")
    assert last_poll < cli.calls.index(cli.deletes[2])
    assert SERVICE_GROUP_ID in cli.deleted_service_groups
    assert f"{ENV_NAME}-base" in cli.deleted_deployments


def test_service_group_delete_204_needs_no_poll(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.http_responses = [(204, {})]
    assert cleanup.main() == 0
    assert len(cli.http_requests) == 1
    assert cli.http_requests[0].get_method() == "DELETE"
    assert SERVICE_GROUP_ID in cli.deleted_service_groups


@pytest.mark.parametrize(
    "responses",
    [
        [(500, {})],
        [(200, {})],
        [(202, {})],
        [(202, {"Location": " "})],
        [(202, {"Location": OPERATION_URL}), (403, {})],
        [(202, {"Location": OPERATION_URL}), (404, {})],
        [(202, {"Location": OPERATION_URL}), (500, {})],
        [(202, {"Location": OPERATION_URL}), (201, {})],
        [(202, {"Location": OPERATION_URL, "Retry-After": "-1"})],
        [(202, {"Location": OPERATION_URL, "Retry-After": "invalid"})],
        [
            (202, {"Location": OPERATION_URL}),
            urllib.error.URLError("connection failed"),
        ],
        [(202, {"Location": OPERATION_URL}), TimeoutError("timed out")],
        [(202, {"Location": OPERATION_URL}), HTTPException("invalid HTTP response")],
    ],
)
def test_service_group_operation_failure_retains_base_records(
    cleanup: ModuleType,
    cli: AzureCli,
    responses: list[tuple[int, dict[str, str]] | OSError | HTTPException],
) -> None:
    cli.http_responses = responses.copy()
    assert cleanup.main() == 1
    assert cli.deleted_rg
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments
    assert SERVICE_GROUP_ID not in cli.deleted_service_groups
    assert (
        sum(
            args[1] == "rest"
            and args[args.index("--method") + 1] == "get"
            and args[args.index("--url") + 1].partition("?")[0] == SERVICE_GROUP_URL
            for args in cli.calls
        )
        == 1
    )


def test_service_group_operation_deadline_caps_retry_after(
    cleanup: ModuleType,
    cli: AzureCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cleanup, "SERVICE_GROUP_DELETE_TIMEOUT_SECONDS", 10)
    cli.http_responses = [
        (202, {"Location": OPERATION_URL, "Retry-After": "3"}),
        (202, {"Retry-After": "1000"}),
    ]
    assert cleanup.main() == 1
    assert cli.now == 10
    assert cli.sleeps == [3, 7]
    assert cli.http_timeouts == [10, 7]
    assert cli.deleted_rg
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments


def test_service_group_relative_location_is_resolved_on_arm(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.http_responses = [
        (202, {"Location": OPERATION_URL.removeprefix("https://management.azure.com")})
    ]
    assert cleanup.main() == 0
    assert cli.http_requests[1].full_url == OPERATION_URL


@pytest.mark.parametrize(
    "location",
    [
        "https://other.example/operation",
        "http://management.azure.com/operation",
        "//other.example/operation",
        "https://management.azure.com@other.example/operation",
        "https://[invalid",
        OPERATION_URL + "#fragment",
    ],
)
def test_service_group_location_cannot_send_credentials_outside_arm(
    cleanup: ModuleType, cli: AzureCli, location: str
) -> None:
    cli.http_responses = [(202, {"Location": location})]
    assert cleanup.main() == 1
    assert len(cli.http_requests) == 1
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments


@pytest.mark.parametrize(("method", "status"), [("GET", 302), ("DELETE", 303)])
def test_service_group_http_redirects_are_not_followed(
    cleanup: ModuleType, method: str, status: int
) -> None:
    headers = Message()
    headers["Location"] = "https://other.example/operation"
    request = urllib.request.Request(
        SERVICE_GROUP_URL,
        headers={"Authorization": "Bearer fake-access-value"},
        method=method,
    )
    opener = urllib.request.build_opener(cleanup.NoRedirectHandler())
    with pytest.raises(urllib.error.HTTPError):
        opener.error("http", request, io.BytesIO(), status, "redirect", headers)


@pytest.mark.parametrize("value", ["", "invalid access value"])
def test_invalid_arm_token_does_not_issue_http_requests(
    cleanup: ModuleType, cli: AzureCli, value: str
) -> None:
    cli.responses["account get-access-token"] = [(0, value, "")]
    assert cleanup.main() == 1
    assert cli.http_requests == []
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments


def test_failed_subscription_lookup_rejects_stdout(
    cleanup: ModuleType, cli: AzureCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID")
    monkeypatch.setattr(cleanup.shutil, "which", lambda name: name)
    cli.azd_result = subprocess.CompletedProcess([], 1, "wrong-subscription", "")
    assert cleanup.main() == 1
    assert cli.calls == [["azd", "env", "get-value", "AZURE_SUBSCRIPTION_ID"]]
    assert cli.deletes == []


def test_empty_subscription_does_not_fall_back_to_azd(
    cleanup: ModuleType, cli: AzureCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "")
    monkeypatch.setattr(cleanup.shutil, "which", lambda name: name)
    cli.azd_result = subprocess.CompletedProcess([], 0, "wrong-subscription", "")
    assert cleanup.main() == 1
    assert cli.calls == []


def test_subscription_lookup_oserror_stops_cleanup(
    cleanup: ModuleType,
    cli: AzureCli,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_name: str) -> str:
        raise OSError("cannot start azd")

    monkeypatch.setattr(cleanup, "get_env_value", fail)
    assert cleanup.main() == 1
    assert cli.deletes == []
    assert "cannot start azd" in capsys.readouterr().err


def test_all_operations_use_selected_subscription_in_cleanup_order(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    assert cleanup.main() == 0
    assert cli.default_subscription != SUBSCRIPTION
    assert cli.selected_subscriptions
    assert set(cli.selected_subscriptions) == {SUBSCRIPTION}
    assert len(cli.deletes) == 6
    assert SLI_ID in cli.deletes[0][cli.deletes[0].index("--url") + 1]
    assert SERVICE_GROUP_ID in cli.deletes[1][cli.deletes[1].index("--url") + 1]
    assert DCRA_ID in cli.deletes[2][cli.deletes[2].index("--url") + 1]
    assert f"{ENV_NAME}-sli" in cli.deletes[3]
    assert cli.deletes[4][1:3] == ["group", "delete"]
    assert RESOURCE_GROUP in cli.deletes[4]
    assert f"{ENV_NAME}-base" in cli.deletes[5]
    assert all(args[1:3] != ["account", "set"] for args in cli.calls)


@pytest.mark.parametrize("use_env", [False, True])
def test_dry_run_discovers_without_deleting(
    cleanup: ModuleType,
    cli: AzureCli,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    use_env: bool,
) -> None:
    if use_env:
        monkeypatch.setenv("AZURE_MONITOR_SLI_CLEANUP_DRY_RUN", "true")
    else:
        monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--dry-run"])
    assert cleanup.main() == 0
    assert cli.deletes == []
    assert set(cli.selected_subscriptions) == {SUBSCRIPTION}
    assert capsys.readouterr().err.count("dry-run: would") == 6
    assert cli.http_requests == []
    assert all(args[1:3] != ["account", "get-access-token"] for args in cli.calls)


@pytest.mark.parametrize(
    "service_group_id",
    [
        "/providers/Microsoft.Management/serviceGroups/sg-aks-chaos-lab-other-abc123",
        f"/subscriptions/other{SERVICE_GROUP_ID}",
        f"https://other.example{SERVICE_GROUP_ID}",
        f"{SERVICE_GROUP_ID}?other=true",
    ],
)
def test_unowned_service_group_and_resource_group_are_not_deleted(
    cleanup: ModuleType,
    cli: AzureCli,
    monkeypatch: pytest.MonkeyPatch,
    service_group_id: str,
) -> None:
    monkeypatch.setenv("AZURE_MONITOR_SLI_SERVICE_GROUP_ID", service_group_id)
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-other")
    assert cleanup.main() == 1
    assert cli.deletes == []
    assert all("rest" not in args for args in cli.calls)
    assert all("rg-other" not in args for args in cli.calls)
    assert all(
        "other-sli" not in args and "other-base" not in args for args in cli.deletes
    )


def test_discovery_checks_id_ownership_not_just_name(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.service_groups = [
        {
            "name": SERVICE_GROUP,
            "id": "/providers/Microsoft.Management/serviceGroups/other",
        }
    ]
    assert cleanup.main() == 0
    assert all(
        "/serviceGroups/" not in args[args.index("--url") + 1]
        for args in cli.deletes
        if "rest" in args
    )


@pytest.mark.parametrize(
    "sli_id",
    [
        f"/subscriptions/other{SLI_ID}",
        "/providers/Microsoft.Management/serviceGroups/other/providers/Microsoft.Monitor/slis/a",
        f"https://other.example{SLI_ID}",
    ],
)
def test_foreign_sli_id_fails_before_service_group_deletion(
    cleanup: ModuleType, cli: AzureCli, sli_id: str
) -> None:
    cli.sli_ids = [SLI_ID, sli_id]
    assert cleanup.main() == 1
    assert cli.deleted_rg
    assert all(
        "/serviceGroups/" not in args[args.index("--url") + 1]
        for args in cli.deletes
        if "rest" in args
    )


@pytest.mark.parametrize(
    "cluster_id",
    [
        CLUSTER_ID.replace(SUBSCRIPTION, "other"),
        CLUSTER_ID.replace(RESOURCE_GROUP, "rg-other"),
        f"https://other.example{CLUSTER_ID}",
    ],
)
def test_foreign_cluster_id_never_used_for_dcra_deletion(
    cleanup: ModuleType, cli: AzureCli, cluster_id: str
) -> None:
    cli.service_groups = []
    cli.cluster_id = cluster_id
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()
    assert cli.deletes == []


@pytest.mark.parametrize(
    "failure",
    [
        ("deployment", "sub", "delete"),
        ("deployment", "sub", "list"),
        ("group", "exists"),
        ("group", "delete"),
    ],
)
def test_failed_cleanup_is_not_reported_as_success(
    cleanup: ModuleType, cli: AzureCli, failure: tuple[str, ...]
) -> None:
    cli.failure = failure
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()


def test_workaround_flags_preserve_skips(
    cleanup: ModuleType, cli: AzureCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_MONITOR_SLI_FIX_SLI_VOID", "false")
    monkeypatch.setenv("AZURE_MONITOR_SLI_FIX_BASE_VOID", "false")
    assert cleanup.main() == 0
    assert len(cli.deletes) == 3
    assert all(args[1] not in ("group", "deployment") for args in cli.deletes)


@pytest.mark.parametrize(
    ("operation", "stdout", "expected_deletes"),
    [
        ("group exists", "false", 0),
        ("resource list", "[]", 1),
        (f"get {DCRA_LIST_URL}", '{"value": []}', 1),
        (
            f"get {DCRA_LIST_URL}",
            json.dumps(
                {
                    "value": [
                        {"id": DCRA_ID.replace("OtlpAppInsightsExtension", "other")}
                    ]
                }
            ),
            1,
        ),
    ],
)
def test_confirmed_absence_skips_dcra_deletion(
    cleanup: ModuleType,
    cli: AzureCli,
    operation: str,
    stdout: str,
    expected_deletes: int,
) -> None:
    cli.service_groups = []
    if operation == "group exists":
        cli.deleted_rg = True
    cli.responses[operation] = [(0, stdout, "")]
    assert cleanup.main() == 0
    assert not cli.deleted_dcra
    assert (
        sum(args[1:3] == ["group", "delete"] for args in cli.deletes)
        == expected_deletes
    )


@pytest.mark.parametrize(
    "operation", ["group exists", "resource list", f"get {DCRA_LIST_URL}"]
)
@pytest.mark.parametrize(
    "response",
    [
        (1, "", "Forbidden"),
        (1, "", "Connection timed out"),
        (0, "not JSON", ""),
        (0, "", ""),
        (0, "{}", ""),
        (0, "[{}]", ""),
        (0, '{"value": [{}]}', ""),
    ],
)
def test_failed_or_invalid_existence_check_blocks_rg_deletion(
    cleanup: ModuleType,
    cli: AzureCli,
    operation: str,
    response: tuple[int, str, str],
) -> None:
    cli.service_groups = []
    cli.responses[operation] = [response]
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()
    assert cli.deletes == []


@pytest.mark.parametrize("response", [(1, "", "Forbidden"), (0, "{}", "")])
def test_dcra_poll_failure_blocks_rg_deletion(
    cleanup: ModuleType, cli: AzureCli, response: tuple[int, str, str]
) -> None:
    cli.service_groups = []
    cli.responses[f"get {DCRA_LIST_URL}"] = [
        (0, json.dumps({"value": [{"id": DCRA_ID}]}), ""),
        response,
    ]
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()
    assert cli.deleted_dcra
    assert not cli.deleted_rg
    assert len(cli.deletes) == 1


@pytest.mark.parametrize(
    "operation",
    [
        "deployment operation",
        f"get {SERVICE_GROUP_URL}",
        f"get {SLI_LIST_URL}",
        f"delete https://management.azure.com{SLI_ID}",
    ],
)
def test_service_group_failure_continues_subscription_cleanup_and_returns_failure(
    cleanup: ModuleType,
    cli: AzureCli,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    cli.responses[operation] = [(1, "", "Forbidden")]
    assert cleanup.main() == 1
    assert cli.deleted_dcra
    assert cli.deleted_rg
    assert f"{ENV_NAME}-sli" in cli.deleted_deployments
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments
    stderr = capsys.readouterr().err
    assert "Forbidden" in stderr
    assert "Service Group cleanup failed" in stderr


@pytest.mark.parametrize(
    "operation",
    ["deployment operation", f"get {SERVICE_GROUP_URL}", f"get {SLI_LIST_URL}"],
)
def test_invalid_service_group_response_is_reported_as_failure(
    cleanup: ModuleType, cli: AzureCli, operation: str
) -> None:
    cli.responses[operation] = [(0, "{}", "")]
    assert cleanup.main() == 1
    assert cli.deleted_rg


def test_service_group_and_dcra_failures_block_rg_deletion(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.failure = ("rest",)
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()
    assert cli.deletes == []


def test_dcra_list_checks_next_page_before_concluding_absence(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.service_groups = []
    next_link = (
        f"{DCRA_LIST_URL}?api-version="
        f"{cleanup.DATA_COLLECTION_RULE_ASSOCIATION_API_VERSION}&$skiptoken=next"
    )
    cli.responses[f"get {DCRA_LIST_URL}"] = [
        (0, json.dumps({"value": [], "nextLink": next_link}), ""),
        (0, json.dumps({"value": [{"id": DCRA_ID}]}), ""),
    ]
    assert cleanup.main() == 0
    assert any(next_link in args for args in cli.calls)
    assert cli.deleted_dcra
    assert cli.deleted_rg


@pytest.mark.parametrize("next_link", [42, "https://other.example/associations"])
def test_invalid_dcra_next_link_blocks_rg_deletion(
    cleanup: ModuleType, cli: AzureCli, next_link: int | str
) -> None:
    cli.service_groups = []
    cli.responses[f"get {DCRA_LIST_URL}"] = [
        (0, json.dumps({"value": [], "nextLink": next_link}), ""),
    ]
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()
    assert cli.deletes == []


def test_dcra_still_present_after_polling_blocks_rg_deletion(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.service_groups = []
    cli.responses[f"get {DCRA_LIST_URL}"] = [
        (0, json.dumps({"value": [{"id": DCRA_ID}]}), "")
    ] * 13
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()
    assert cli.deleted_dcra
    assert not cli.deleted_rg
    assert cli.responses[f"get {DCRA_LIST_URL}"] == []


@pytest.mark.parametrize("explicit_id", [False, True])
def test_retry_after_service_group_deleted_succeeds(
    cleanup: ModuleType,
    cli: AzureCli,
    monkeypatch: pytest.MonkeyPatch,
    explicit_id: bool,
) -> None:
    if explicit_id:
        monkeypatch.setenv("AZURE_MONITOR_SLI_SERVICE_GROUP_ID", SERVICE_GROUP_ID)
    cli.responses[f"get {DCRA_LIST_URL}"] = [(1, "", "Connection timed out")]
    with pytest.raises(SystemExit, match="1"):
        cleanup.main()
    assert SERVICE_GROUP_ID in cli.deleted_service_groups
    assert not cli.deleted_rg
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments

    calls_before_retry = len(cli.calls)
    assert cleanup.main() == 0
    assert cli.deleted_rg
    assert f"{ENV_NAME}-base" in cli.deleted_deployments
    assert not any(
        SLI_LIST_URL in " ".join(args) for args in cli.calls[calls_before_retry:]
    )
    deletes_after_retry = len(cli.deletes)
    assert cleanup.main() == 0
    assert len(cli.deletes) == deletes_after_retry


def test_service_group_failure_preserves_discovery_until_retry(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.http_responses = [(202, {"Location": OPERATION_URL}), (500, {})]
    assert cleanup.main() == 1
    assert cli.deleted_rg
    assert SERVICE_GROUP_ID not in cli.deleted_service_groups
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments

    assert cleanup.main() == 0
    assert SERVICE_GROUP_ID in cli.deleted_service_groups
    assert f"{ENV_NAME}-base" in cli.deleted_deployments


@pytest.mark.parametrize(
    "response",
    [
        (1, "", 'ERROR: Forbidden({"error":{"code":"AuthorizationFailed"}})'),
        (1, "", 'ERROR: Not Found("")'),
        (1, "", 'ERROR: Not Found({"error":{"code":"AuthorizationFailed"}})'),
        (1, "", 'ERROR: Not Found({"error":null})'),
        (1, "", "ERROR: Not Found(invalid JSON)"),
        (1, "", "Connection timed out"),
        (0, "", ""),
        (0, "{}", ""),
        (0, json.dumps({"id": SERVICE_GROUP_ID + "-other"}), ""),
    ],
)
def test_only_confirmed_service_group_absence_is_skipped(
    cleanup: ModuleType, cli: AzureCli, response: tuple[int, str, str]
) -> None:
    cli.responses[f"get {SERVICE_GROUP_URL}"] = [response]
    assert cleanup.main() == 1
    assert cli.deleted_rg
    assert not cli.deleted_service_groups
    assert not cli.deleted_slis
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments


def test_sli_authorization_failure_is_not_treated_as_absent_service_group(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.responses[f"get {SLI_LIST_URL}"] = [
        (1, "", 'ERROR: Forbidden({"error":{"code":"AuthorizationFailed"}})')
    ]
    assert cleanup.main() == 1
    assert not cli.deleted_service_groups
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments


@pytest.mark.parametrize("payload", [None, {}, [None], [""]])
def test_invalid_deployment_discovery_response_fails(
    cleanup: ModuleType, cli: AzureCli, payload: Any
) -> None:
    cli.responses["deployment operation"] = [(0, json.dumps(payload), "")]
    assert cleanup.main() == 1
    assert cli.deleted_rg
    assert f"{ENV_NAME}-base" not in cli.deleted_deployments


def test_discovery_deduplicates_service_group_ids(
    cleanup: ModuleType, cli: AzureCli
) -> None:
    cli.responses["deployment operation"] = [
        (0, json.dumps([SERVICE_GROUP_ID, SERVICE_GROUP_ID]), "")
    ]
    assert cleanup.main() == 0
    assert (
        sum(
            args[0] == "http"
            and args[args.index("--method") + 1] == "delete"
            and args[args.index("--url") + 1].partition("?")[0] == SERVICE_GROUP_URL
            for args in cli.deletes
        )
        == 1
    )
