#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ARM_API_VERSION = "2026-01-01"
DATA_PLANE_AUDIENCE = "https://azuresre.dev"
APP_NAME = "aks-chaos-lab"
PLAN_TITLE_FILTERS = {
    "aks-chaos-lab-sli-short-window-review": "SLI short-window alert",
}
LEGACY_PLAN_NAMES = {
    "aks-chaos-lab-fleet-review",
    "aks-chaos-lab-kube-review",
    "aks-chaos-lab-publisher-review",
    "aks-chaos-lab-sli-review",
    "aks-chaos-lab-sli-fast-burn-review",
    "aks-chaos-lab-sli-slow-burn-review",
}
INCIDENT_FILTERS_PATH = "/api/v2/extendedAgent/incidentFilters"
PLATFORM_STATUS_PATH = "/api/v1/incidentplayground/incidentPlatformType"


class ConfigurationError(Exception):
    pass


class ApiRequestError(ConfigurationError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class TransientApiError(ConfigurationError):
    pass


def remaining_timeout(
    deadline: float | None, limit: float, operation: str = "処理"
) -> float:
    if deadline is None:
        return limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ConfigurationError(f"{operation}の制限時間に達しました。")
    return min(remaining, limit)


def make_incident_filter(title_contains: str) -> dict[str, Any]:
    return {
        "incidentPlatform": "AzMonitor",
        "priorities": ["Sev3"],
        "titleContains": title_contains,
        "agentMode": "review",
        "handlingAgent": "meta_agent",
        "isEnabled": True,
        "maxAutomatedInvestigationAttempts": 1,
        "mergeEnabled": True,
        "mergeWindowHours": 3,
    }


def _run_az(
    arguments: list[str], *, secret_output: bool = False, deadline: float | None = None
) -> str:
    az_path = shutil.which("az")
    if az_path is None:
        raise ConfigurationError("Azure CLI (`az`) is required.")
    timeout = remaining_timeout(deadline, 60)
    try:
        result = subprocess.run(
            [az_path, *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        operation = "トークン取得" if secret_output else " ".join(arguments[:2])
        raise ConfigurationError(
            f"Azure CLI {operation} が {timeout:.1f} 秒以内に応答しませんでした。"
        ) from error
    except FileNotFoundError as error:
        raise ConfigurationError("Azure CLI (`az`) is required.") from error
    if result.returncode:
        detail = result.stderr.strip()
        if secret_output:
            detail = "Azure CLI could not issue an SRE Agent access token."
        raise ConfigurationError(
            detail or f"Azure CLI exited with {result.returncode}."
        )
    return result.stdout.strip()


def _request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    content_type: str = "application/json",
    deadline: float | None = None,
) -> Any:
    data = (
        raw_body
        if raw_body is not None
        else (json.dumps(body).encode("utf-8") if body is not None else None)
    )
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=remaining_timeout(deadline, 30)
        ) as response:
            response_body = response.read()
    except TimeoutError as error:
        raise TransientApiError(
            f"SRE Agent API {method} {urllib.parse.urlsplit(url).path} timed out."
        ) from error
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ApiRequestError(
            error.code,
            f"SRE Agent API {method} failed with HTTP {error.code}: {detail[:500]}",
        ) from error
    except urllib.error.URLError as error:
        raise TransientApiError(
            f"SRE Agent API {method} failed: {error.reason}"
        ) from error
    if not response_body:
        return {}
    try:
        return json.loads(response_body)
    except json.JSONDecodeError as error:
        raise ConfigurationError("SRE Agent API returned invalid JSON.") from error


def _filter_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
        raise ConfigurationError("Incident filter list returned an unexpected shape.")
    items = []
    for item in payload["value"]:
        if not isinstance(item, dict):
            raise ConfigurationError("Incident filter list contains an invalid item.")
        if not isinstance(item.get("name"), str):
            raise ConfigurationError("Incident filter is missing its name.")
        properties = item.get("properties")
        if not isinstance(properties, dict):
            raise ConfigurationError(
                f"Incident filter {item['name']} has no properties."
            )
        if not isinstance(properties.get("isEnabled"), bool) or not isinstance(
            properties.get("agentMode"), str
        ):
            raise ConfigurationError(
                f"Incident filter {item['name']} has no explicit enabled state or mode."
            )
        items.append(item)
    return items


def _properties(item: dict[str, Any]) -> dict[str, Any]:
    properties = item.get("properties")
    return properties if isinstance(properties, dict) else {}


def _is_enabled(item: dict[str, Any]) -> bool:
    return _properties(item).get("isEnabled") is True


def _is_autonomous(item: dict[str, Any]) -> bool:
    return str(_properties(item).get("agentMode", "")).casefold() == "autonomous"


def _is_quickstart(item: dict[str, Any]) -> bool:
    return "quickstart" in str(item.get("name", "")).casefold()


def _get_filters(endpoint: str, token: str) -> list[dict[str, Any]]:
    url = f"{endpoint}{INCIDENT_FILTERS_PATH}"
    items: list[dict[str, Any]] = []
    while url:
        payload = _request_json(url, token)
        items.extend(_filter_items(payload))
        next_link = payload.get("nextLink")
        url = str(next_link) if next_link else ""
    return items


def _put_filter(
    endpoint: str,
    token: str,
    name: str,
    properties: dict[str, Any],
    *,
    tags: list[Any] | None = None,
) -> None:
    url = f"{endpoint}{INCIDENT_FILTERS_PATH}/{urllib.parse.quote(name, safe='')}"
    body = {
        "name": name,
        "type": "IncidentFilter",
        "tags": tags or [],
        "properties": properties,
    }
    for attempt in range(4):
        try:
            _request_json(url, token, method="PUT", body=body)
        except ApiRequestError as error:
            if (
                error.status_code not in {404, 409, 429, 500, 502, 503, 504}
                or attempt == 3
            ):
                raise
            last_error: ConfigurationError = error
        except TransientApiError as error:
            if attempt == 3:
                raise
            last_error = error
        else:
            return
        print(f"Retrying incident response plan {name} after API initialization delay.")
        time.sleep(30)
    raise last_error


def _delete_filter(endpoint: str, token: str, name: str) -> None:
    url = f"{endpoint}{INCIDENT_FILTERS_PATH}/{urllib.parse.quote(name, safe='')}"
    for attempt in range(4):
        try:
            _request_json(url, token, method="DELETE")
        except ApiRequestError as error:
            if error.status_code == 404:
                return
            if error.status_code not in {409, 429, 500, 502, 503, 504} or attempt == 3:
                raise
            last_error: ConfigurationError = error
        except TransientApiError as error:
            if attempt == 3:
                raise
            last_error = error
        else:
            return
        print(f"Retrying incident response plan {name} deletion after API failure.")
        time.sleep(30)
    raise last_error


def _disable_quickstart_plans(
    endpoint: str,
    token: str,
    items: list[dict[str, Any]],
) -> None:
    for item in items:
        if not _is_quickstart(item) or not _is_enabled(item):
            continue
        properties = dict(_properties(item))
        properties["isEnabled"] = False
        _put_filter(
            endpoint,
            token,
            str(item["name"]),
            properties,
            tags=item.get("tags") if isinstance(item.get("tags"), list) else [],
        )
        print(f"Disabled quickstart incident response plan: {item['name']}")


def _delete_legacy_plans(
    endpoint: str,
    token: str,
    items: list[dict[str, Any]],
) -> None:
    for item in items:
        name = str(item["name"])
        if name not in LEGACY_PLAN_NAMES:
            continue
        _delete_filter(endpoint, token, name)
        print(f"Deleted obsolete incident response plan: {name}")


def _wait_for_azure_monitor(endpoint: str, token: str) -> None:
    url = f"{endpoint}{PLATFORM_STATUS_PATH}"
    deadline = time.monotonic() + 300
    last_status = "not available"
    while time.monotonic() < deadline:
        try:
            payload = _request_json(url, token)
        except ApiRequestError as error:
            if error.status_code not in {404, 409, 429, 500, 502, 503, 504}:
                raise
            last_status = str(error)
        except TransientApiError as error:
            last_status = str(error)
        else:
            if not isinstance(payload, dict):
                raise ConfigurationError(
                    "Incident platform status returned an unexpected shape."
                )
            platform_type = payload.get("incidentPlatformType")
            if platform_type == "AzMonitor":
                return
            last_status = str(platform_type or "not configured")
        time.sleep(10)
    raise ConfigurationError(
        f"Azure Monitor incident platform did not become ready within 5 minutes: {last_status}"
    )


def _verify_filters(endpoint: str, token: str) -> None:
    deadline = time.monotonic() + 60
    last_status = "not available"
    while time.monotonic() < deadline:
        filters = _get_filters(endpoint, token)
        plans_match = True
        for name, title_contains in PLAN_TITLE_FILTERS.items():
            plan = next((item for item in filters if item.get("name") == name), None)
            if plan is None:
                last_status = f"incident response plan {name} was not returned"
                plans_match = False
                break
            properties = _properties(plan)
            if not (
                _is_enabled(plan)
                and str(properties.get("agentMode", "")).casefold() == "review"
                and properties.get("incidentPlatform") == "AzMonitor"
                and properties.get("priorities") == ["Sev3"]
                and properties.get("titleContains") == title_contains
            ):
                last_status = f"incident response plan {name} has unexpected settings"
                plans_match = False
                break
        if plans_match:
            remaining_legacy_plans = [
                name
                for name in LEGACY_PLAN_NAMES
                if any(item.get("name") == name for item in filters)
            ]
            if remaining_legacy_plans:
                last_status = "obsolete incident response plans remain: " + ", ".join(
                    sorted(remaining_legacy_plans)
                )
                plans_match = False
        if plans_match:
            enabled_autonomous = [
                str(item["name"])
                for item in filters
                if _is_enabled(item) and _is_autonomous(item)
            ]
            if enabled_autonomous:
                raise ConfigurationError(
                    "Enabled Autonomous response plans remain after configuration: "
                    + ", ".join(enabled_autonomous)
                )
            return
        time.sleep(5)
    raise ConfigurationError(
        f"Incident response plan verification timed out after 60 seconds: {last_status}"
    )


def agent_connection(
    *, deadline: float | None = None
) -> tuple[str, str, dict[str, Any]]:
    environment = os.environ.get("AZURE_ENV_NAME", "").strip()
    if not environment:
        raise ConfigurationError(
            "AZURE_ENV_NAME is required when SRE Agent is enabled."
        )
    if os.environ.get("AZURE_SRE_AGENT_ENABLED", "").casefold() != "true":
        raise ConfigurationError(
            "AZURE_SRE_AGENT_ENABLED must be true when Azure Monitor incident integration is enabled."
        )

    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
    if not subscription:
        raise ConfigurationError(
            "AZURE_SUBSCRIPTION_ID is required when incident integration is enabled."
        )
    resource_group = os.environ.get(
        "AZURE_RESOURCE_GROUP", f"rg-{APP_NAME}-{environment}"
    ).strip()
    agent_name = f"sre-{APP_NAME}-{environment}"
    agent_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.App/agents/{agent_name}"
    )
    agent = json.loads(
        _run_az(
            [
                "resource",
                "show",
                "--ids",
                agent_id,
                "--subscription",
                subscription,
                "--api-version",
                ARM_API_VERSION,
                "--output",
                "json",
            ],
            deadline=deadline,
        )
    )
    properties = agent.get("properties", {})
    if not isinstance(properties, dict):
        raise ConfigurationError(f"Invalid Agent properties for {agent_id}.")
    endpoint = str(properties.get("agentEndpoint", "")).rstrip("/")
    if not endpoint.startswith("https://"):
        raise ConfigurationError(f"Agent endpoint was not returned for {agent_id}.")

    token = _run_az(
        [
            "account",
            "get-access-token",
            "--resource",
            DATA_PLANE_AUDIENCE,
            "--query",
            "accessToken",
            "--output",
            "tsv",
            "--subscription",
            subscription,
        ],
        secret_output=True,
        deadline=deadline,
    )
    if not token:
        raise ConfigurationError("Azure CLI returned an empty SRE Agent access token.")
    return endpoint, token, properties


def _configure() -> None:
    endpoint, token, properties = agent_connection()
    incident_config = properties.get("incidentManagementConfiguration")
    if (
        not isinstance(incident_config, dict)
        or incident_config.get("type") != "AzMonitor"
    ):
        raise ConfigurationError("Azure Monitor incident platform is not configured.")

    _wait_for_azure_monitor(endpoint, token)
    filters = _get_filters(endpoint, token)
    _disable_quickstart_plans(endpoint, token, filters)
    _delete_legacy_plans(endpoint, token, filters)

    autonomous = [
        str(item["name"])
        for item in filters
        if _is_enabled(item) and _is_autonomous(item) and not _is_quickstart(item)
    ]
    if autonomous:
        raise ConfigurationError(
            "Refusing to continue while other enabled Autonomous response plans exist: "
            + ", ".join(autonomous)
        )

    for name, title_contains in PLAN_TITLE_FILTERS.items():
        _put_filter(endpoint, token, name, make_incident_filter(title_contains))

    _verify_filters(endpoint, token)
    print("Configured Review response plan for Sev3 SLI short-window alerts.")


def main() -> int:
    if (
        os.environ.get(
            "AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED", "false"
        ).casefold()
        != "true"
    ):
        print("Azure SRE Agent Azure Monitor incident integration is disabled.")
        return 0
    try:
        _configure()
    except ConfigurationError as error:
        print(
            f"Azure SRE Agent incident configuration failed: {error}", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
