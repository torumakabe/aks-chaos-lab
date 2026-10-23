#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import configure_sre_agent_incidents as incidents  # noqa: E402

ROOT = SCRIPTS.parent
KNOWLEDGE_NAME = "aks-chaos-lab-readme.md"
MEMORY_PATH = "/api/v1/AgentMemory"
DOMAIN_PATH = "/api/v2/github/domains/github_com"
REPO_PATH = "/api/v2/repos/aks-chaos-lab"


def read_client_id() -> str:
    try:
        value = input(
            "GitHub App の Client ID（Iv で始まる値。秘密鍵や Client secret "
            "ではありません）を貼り付け、Enter を押してください: "
        )
    except (EOFError, OSError) as error:
        raise incidents.ConfigurationError(
            "Client ID を入力できません。azd exec --interactive で実行するか、"
            "--client-id に GitHub App の Client ID を指定してください。"
        ) from error
    if not value:
        raise incidents.ConfigurationError("Client ID の入力が終了しました。")
    return value.strip()


def azure_json(arguments: list[str], *, deadline: float | None = None) -> Any:
    return json.loads(
        incidents._run_az([*arguments, "--output", "json"], deadline=deadline)
    )


def audit_identity_matches(value: Any, principal: str) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return False
    if not isinstance(value, dict):
        return False
    return any(
        (
            key.casefold() in {"oid", "objectidentifier", "objectid"}
            or key.casefold().endswith("/objectidentifier")
        )
        and isinstance(item, str)
        and item.casefold() == principal.casefold()
        or isinstance(item, dict)
        and audit_identity_matches(item, principal)
        for key, item in value.items()
    )


def verify_github_audit(
    since: datetime | None = None, key_uri: str | None = None
) -> None:
    deadline = time.monotonic() + 120
    start = since or datetime.now(UTC) - timedelta(minutes=30)
    end = datetime.now(UTC)
    if start.tzinfo is None or start >= end:
        raise incidents.ConfigurationError(
            "--since must be a past timestamp with a timezone."
        )
    names = (
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_SRE_AGENT_ID",
        "AZURE_SRE_AGENT_VAULT_ID",
        "AZURE_SRE_AGENT_VAULT_URI",
        "AZURE_LOG_ANALYTICS_WORKSPACE_ID",
    )
    values = [os.environ.get(name, "").strip() for name in names]
    if not all(values):
        raise incidents.ConfigurationError(
            "Audit verification requires azd outputs: " + ", ".join(names)
        )
    subscription, agent_id, vault_id, vault_uri, workspace = values
    agent = azure_json(
        [
            "resource",
            "show",
            "--ids",
            agent_id,
            "--subscription",
            subscription,
            "--api-version",
            incidents.ARM_API_VERSION,
        ],
        deadline=deadline,
    )
    principal = agent.get("identity", {}).get("principalId")
    subnet_id = (
        agent.get("properties", {}).get("vnetConfiguration", {}).get("subnetResourceId")
    )
    if (
        not isinstance(principal, str)
        or not principal
        or not isinstance(subnet_id, str)
        or not subnet_id
    ):
        raise incidents.ConfigurationError("Agent identity or subnet was not returned.")
    subnet = azure_json(
        [
            "resource",
            "show",
            "--ids",
            subnet_id,
            "--subscription",
            subscription,
            "--api-version",
            "2024-05-01",
        ],
        deadline=deadline,
    )
    properties = subnet.get("properties", {})
    prefixes = properties.get("addressPrefixes") or [properties.get("addressPrefix")]
    if (
        not isinstance(prefixes, list)
        or not prefixes
        or not all(isinstance(item, str) for item in prefixes)
    ):
        raise incidents.ConfigurationError("Agent subnet prefixes were not returned.")
    networks = [ipaddress.ip_network(item) for item in prefixes]
    start_text = start.astimezone(UTC).isoformat()
    end_text = end.isoformat()
    # azd の出力も KQL の文字列リテラルとしてエスケープする。
    literal = json.dumps(vault_id)
    query = f"""
AZKVAuditLogs
| where TimeGenerated between (datetime({start_text}) .. datetime({end_text}))
| where _ResourceId =~ {literal}
| where OperationName =~ "KeySign" and ResultType =~ "Success"
| project TimeGenerated, Identity, CallerIpAddress, RequestUri
| order by TimeGenerated desc
"""
    print(f"署名監査を確認します: {start_text} ～ {end_text}", flush=True)
    rows = azure_json(
        [
            "monitor",
            "log-analytics",
            "query",
            "--subscription",
            subscription,
            "--workspace",
            workspace,
            "--analytics-query",
            query,
            "--timespan",
            f"{start_text}/{end_text}",
        ],
        deadline=deadline,
    )
    incidents.remaining_timeout(deadline, 60)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise incidents.ConfigurationError("Invalid Key Vault audit query response.")
    vault_host = urllib.parse.urlsplit(vault_uri).hostname
    if not vault_host:
        raise incidents.ConfigurationError("Invalid Vault URI.")
    expected_key = urllib.parse.urlsplit(
        key_uri or vault_uri.rstrip("/") + "/keys/sre-agent-github-app-key"
    )
    expected_parts = expected_key.path.strip("/").split("/")
    if expected_key.hostname != vault_host:
        raise incidents.ConfigurationError(
            "Audit verification requires the configured Key to belong to the environment Vault."
        )
    for row in rows:
        uri = urllib.parse.urlsplit(str(row.get("RequestUri", "")))
        parts = uri.path.strip("/").split("/")
        if (
            uri.scheme != "https"
            or uri.hostname != vault_host
            or len(parts) != 4
            or parts[:2] != expected_parts[:2]
            or not parts[2]
            or len(expected_parts) == 3
            and parts[2] != expected_parts[2]
            or parts[3].casefold() != "sign"
            or not audit_identity_matches(row.get("Identity"), principal)
        ):
            continue
        address = str(row.get("CallerIpAddress", ""))
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            try:
                ip = ipaddress.ip_address(address.rsplit(":", 1)[0])
            except ValueError:
                continue
        if not any(ip in network for network in networks):
            continue
        print(
            "KeySign 成功を確認しました。"
            f"\n時刻: {row.get('TimeGenerated')}"
            f"\nAgent system identity: {principal}"
            f"\n送信元 IP: {address}"
            f"\nKey: {uri.geturl()}"
        )
        return
    raise incidents.ConfigurationError(
        "指定期間に対象 Key、現在の Agent identity、Agent subnet が一致する "
        "KeySign 成功を確認できませんでした。ログの取り込み待ちの場合は数分後に"
        "同じコマンドを再実行してください。接続失敗とはまだ断定できません。"
    )


def request(
    endpoint: str,
    token: str,
    path: str,
    *,
    deadline: float | None = None,
    **kwargs: Any,
) -> Any:
    operation = f"SRE Agent API {kwargs.get('method', 'GET')} {path}"
    for attempt in range(10):
        if deadline is not None:
            incidents.remaining_timeout(deadline, 30, operation)
            kwargs["deadline"] = deadline
        try:
            result = incidents._request_json(endpoint + path, token, **kwargs)
            if deadline is not None:
                incidents.remaining_timeout(deadline, 30, operation)
            return result
        except incidents.ApiRequestError as error:
            if (
                error.status_code not in {404, 409, 429, 500, 502, 503, 504}
                or attempt == 9
            ):
                raise
        except incidents.TransientApiError:
            if attempt == 9:
                raise
        print(f"API の応答待ち: {path}（次回 {attempt + 2}/10）", flush=True)
        time.sleep(incidents.remaining_timeout(deadline, 15, operation))
    raise incidents.ConfigurationError("SRE Agent API readiness retry exhausted.")


def multipart_readme(content: bytes) -> tuple[bytes, str]:
    boundary = "sre-" + uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{KNOWLEDGE_NAME}"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def configure_knowledge(endpoint: str, token: str) -> None:
    content = (ROOT / "README.md").read_bytes()
    if not content or len(content) > 16 * 1024 * 1024:
        raise incidents.ConfigurationError("README must be nonempty and at most 16 MB.")
    body, content_type = multipart_readme(content)
    result = request(
        endpoint,
        token,
        MEMORY_PATH + "/upload?triggerIndexing=true",
        method="POST",
        raw_body=body,
        content_type=content_type,
    )
    if (
        not isinstance(result, dict)
        or result.get("indexingTriggered") is not True
        or not isinstance(result.get("uploaded"), list)
        or KNOWLEDGE_NAME not in result["uploaded"]
    ):
        raise incidents.ConfigurationError("README upload was not acknowledged.")
    deadline = time.monotonic() + 300
    last_error = ""
    while time.monotonic() < deadline:
        path = MEMORY_PATH + "/files"
        files: list[dict[str, Any]] = []
        seen: set[str] = set()
        while path:
            if time.monotonic() >= deadline:
                raise incidents.ConfigurationError("Knowledge file listing timed out.")
            if path in seen:
                raise incidents.ConfigurationError(
                    "Knowledge pagination repeated a page."
                )
            seen.add(path)
            payload = request(endpoint, token, path)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("files"), list
            ):
                raise incidents.ConfigurationError("Invalid knowledge file list.")
            if any(not isinstance(item, dict) for item in payload["files"]):
                raise incidents.ConfigurationError("Invalid knowledge file metadata.")
            files.extend(payload["files"])
            continuation = payload.get("continuationToken")
            if continuation is not None and not isinstance(continuation, str):
                raise incidents.ConfigurationError(
                    "Invalid knowledge continuation token."
                )
            path = (
                MEMORY_PATH
                + "/files?continuationToken="
                + urllib.parse.quote(continuation, safe="")
                if isinstance(continuation, str) and continuation
                else ""
            )
        matches = [item for item in files if item.get("name") == KNOWLEDGE_NAME]
        if len(matches) > 1:
            raise incidents.ConfigurationError("Duplicate README knowledge files.")
        if matches:
            error = str(matches[0].get("errorReason") or "")
            if matches[0].get("isIndexed") is True and not error:
                print(f"Indexed knowledge file: {KNOWLEDGE_NAME}")
                return
            # The service reports errorReason while it retries indexing and clears
            # it on success, so only an error that remains at the deadline is a failure.
            if error and error != last_error:
                print(
                    f"Knowledge indexing reported an error; waiting for retry: {error}"
                )
            last_error = error
        time.sleep(5)
    if last_error:
        raise incidents.ConfigurationError(f"README indexing failed: {last_error}")
    raise incidents.ConfigurationError(
        "README indexing did not complete within 5 minutes."
    )


def github_payload(client_id: str, key_uri: str) -> dict[str, str]:
    parsed = urllib.parse.urlsplit(key_uri)
    parts = parsed.path.strip("/").split("/")
    if not client_id.startswith("Iv") or not (
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.hostname.endswith(".vault.azure.net")
        and not parsed.username
        and not parsed.password
        and not parsed.port
        and not parsed.query
        and not parsed.fragment
        and len(parts) in {2, 3}
        and parts[0] == "keys"
        and all(parts)
    ):
        raise incidents.ConfigurationError(
            "Supply a GitHub App Client ID (Iv...) and a Key Vault /keys/<name> URI, not a Secret URI."
        )
    return {
        "authType": "GitHubApp",
        "clientId": client_id,
        # The service retains this field name but only accepts Key URIs.
        "privateKeySecretUri": key_uri,
    }


def configure_github(
    endpoint: str,
    token: str,
    client_id: str,
    key_uri: str,
    *,
    deadline: float | None = None,
) -> None:
    if deadline is None:
        deadline = time.monotonic() + 300
    body = github_payload(client_id, key_uri)
    print("GitHub App の接続を設定しています…", flush=True)
    result = request(
        endpoint, token, DOMAIN_PATH, method="PUT", body=body, deadline=deadline
    )
    if not isinstance(result, dict) or result.get("success") is not True:
        raise incidents.ConfigurationError(
            "GitHub App configuration was not acknowledged."
        )
    domains = request(endpoint, token, "/api/v2/github/domains", deadline=deadline)
    if not isinstance(domains, dict) or not isinstance(domains.get("values"), list):
        raise incidents.ConfigurationError("Invalid GitHub domain list.")
    if not any(
        isinstance(item, dict)
        and item.get("name") == "github.com"
        and all(item.get(key) == value for key, value in body.items())
        for item in domains["values"]
    ):
        raise incidents.ConfigurationError("GitHub domain read-back did not match.")
    print(
        "GitHub App の接続設定を確認しました。repository を登録しています…", flush=True
    )
    request(
        endpoint,
        token,
        REPO_PATH,
        deadline=deadline,
        method="PUT",
        body={
            "name": "aks-chaos-lab",
            "type": "CodeRepo",
            "properties": {
                "url": "https://github.com/torumakabe/aks-chaos-lab",
                "type": "GitHub",
                "branch": "main",
            },
        },
    )
    print("repository の Ready を待っています…", flush=True)
    while time.monotonic() < deadline:
        repo = request(endpoint, token, REPO_PATH, deadline=deadline)
        properties = repo.get("properties") if isinstance(repo, dict) else None
        if not isinstance(properties, dict):
            raise incidents.ConfigurationError(
                "Invalid Code Access repository response."
            )
        status = properties.get("cloneStatus")
        if status == "Ready":
            if any(
                properties.get(key) != value
                for key, value in {
                    "url": "https://github.com/torumakabe/aks-chaos-lab",
                    "branch": "main",
                    "type": "GitHub",
                }.items()
            ):
                raise incidents.ConfigurationError(
                    "Repository read-back did not match."
                )
            print("GitHub Code Access repository is Ready.", flush=True)
            return
        if status in {"Failed", "Error"}:
            raise incidents.ConfigurationError(
                f"Repository clone failed: {properties.get('errorMessage')}"
            )
        print(f"repository の状態: {status}。10秒後に再確認します。", flush=True)
        time.sleep(incidents.remaining_timeout(deadline, 10))
    raise incidents.ConfigurationError(
        "Repository clone did not become Ready within 5 minutes."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=["validate", "bootstrap", "github", "verify-github"],
        nargs="?",
        default="bootstrap",
    )
    parser.add_argument("--client-id")
    parser.add_argument("--key-uri")
    parser.add_argument(
        "--since",
        type=datetime.fromisoformat,
        help="Audit start time with timezone (default: 30 minutes ago).",
    )
    args = parser.parse_args(argv)
    try:
        enabled = os.environ.get("AZURE_SRE_AGENT_ENABLED", "false").casefold()
        incident_flag = os.environ.get(
            "AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED", "false"
        ).casefold()
        if incident_flag not in {"true", "false"}:
            raise incidents.ConfigurationError(
                "AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED must be true or false."
            )
        if enabled not in {"true", "false"}:
            raise incidents.ConfigurationError(
                "AZURE_SRE_AGENT_ENABLED must be true or false."
            )
        if enabled != "true":
            if incident_flag == "true":
                raise incidents.ConfigurationError(
                    "Incident integration requires SRE Agent to be enabled."
                )
            if args.operation in {"github", "verify-github"}:
                raise incidents.ConfigurationError(
                    "Enable SRE Agent before configuring GitHub."
                )
            print("Azure SRE Agent is disabled; skipping bootstrap.")
            return 0
        region = os.environ.get("AZURE_LOCATION", "").replace(" ", "").casefold()
        agent_region = (
            os.environ.get("AZURE_SRE_AGENT_LOCATION", "").replace(" ", "").casefold()
        )
        if agent_region not in {"", "none", region}:
            raise incidents.ConfigurationError(
                "AZURE_SRE_AGENT_LOCATION must match AZURE_LOCATION for VNet integration."
            )
        if args.operation == "validate":
            print("SRE Agent configuration inputs are valid.")
            return 0
        if args.operation == "verify-github":
            verify_github_audit(args.since)
            return 0
        if args.operation == "github":
            if not args.key_uri:
                vault_uri = os.environ.get("AZURE_SRE_AGENT_VAULT_URI", "").strip()
                if not vault_uri:
                    raise incidents.ConfigurationError(
                        "AZURE_SRE_AGENT_VAULT_URI or --key-uri is required."
                    )
                args.key_uri = vault_uri.rstrip("/") + "/keys/sre-agent-github-app-key"
            if not args.client_id:
                args.client_id = read_client_id()
            github_payload(args.client_id, args.key_uri)
        if args.operation == "github":
            deadline = time.monotonic() + 300
            print(
                "SRE Agent への接続と認証を確認しています（入力後の共通期限は5分）…",
                flush=True,
            )
            endpoint, token, _ = incidents.agent_connection(deadline=deadline)
            configure_github(
                endpoint, token, args.client_id, args.key_uri, deadline=deadline
            )
            print(
                "GitHub 連携設定が完了しました。repository は Ready です。",
                flush=True,
            )
        else:
            endpoint, token, _ = incidents.agent_connection()
            if incidents.main():
                return 1
            configure_knowledge(endpoint, token)
            print(
                "SRE Agent bootstrap completed. GitHub setup is a separate optional operation."
            )
    except KeyboardInterrupt:
        print(
            "中断しました。保存済みの接続設定と鍵は削除していません。",
            file=sys.stderr,
            flush=True,
        )
        return 130
    except (incidents.ConfigurationError, OSError, ValueError, EOFError) as error:
        print(f"SRE Agent configuration failed: {error}", file=sys.stderr)
        if args.operation == "github":
            print(
                "途中まで保存された設定は取り消していません。"
                "原因を解消して同じコマンドを再実行できます。鍵の再登録は不要です。",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
