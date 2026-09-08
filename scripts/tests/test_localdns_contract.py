from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
NAP_ROOT = REPO_ROOT / "k8s/node-provisioning"
MODE_ENV = "AZURE_AKS_LOCAL_DNS_MODE"


def read_yaml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def test_localdns_mode_binding() -> None:
    expression = f"${{{MODE_ENV}=Required}}"
    parameters = json.loads(
        (REPO_ROOT / "infra/main.parameters.json").read_text(encoding="utf-8")
    )
    config = read_yaml(REPO_ROOT / "azure.yaml")["services"]["node-provisioning"]
    assert config["k8s"]["kustomize"]["dir"] == str(NAP_ROOT.relative_to(REPO_ROOT))
    assert parameters["parameters"]["localDnsMode"]["value"] == expression
    assert config["k8s"]["kustomize"]["env"][MODE_ENV] == expression
    main = (REPO_ROOT / "infra/main.bicep").read_text(encoding="utf-8")
    module = (REPO_ROOT / "infra/modules/aks.bicep").read_text(encoding="utf-8")
    for source in (main, module):
        parameter = re.search(
            r"@allowed\(\[([^]]+)\]\)\s*param localDnsMode string = 'Required'",
            source,
        )
        assert parameter is not None
        assert set(re.findall(r"'([^']+)'", parameter[1])) == {"Disabled", "Required"}
    assert "localDnsMode: localDnsMode" in main
    assert f"output {MODE_ENV} string = localDnsMode" in main
    assert "localDNSProfile: localDnsProfile" in module
    assert re.search(
        r"union\(loadJsonContent\('templates/aks-localdns.json'\),"
        r"\s*\{\s*mode: localDnsMode\s*\}\s*\)",
        module,
    )


@pytest.mark.parametrize("mode", ("Required", "Disabled"))
def test_nap_mode_and_profile(tmp_path: Path, mode: str) -> None:
    if executable := shutil.which("kustomize"):
        command = [executable, "build"]
    elif executable := shutil.which("kubectl"):
        command = [executable, "kustomize"]
    else:
        pytest.skip("kustomize or kubectl is required")
    for source in NAP_ROOT.glob("*.yaml"):
        shutil.copyfile(source, tmp_path / source.name)
    (tmp_path / ".env").write_text(f"{MODE_ENV}={mode}\n", encoding="utf-8")
    result = subprocess.run(
        [*command, str(tmp_path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    documents = list(yaml.safe_load_all(result.stdout))
    assert len(documents) == 2
    manifests = {item["kind"]: item for item in documents}
    assert set(manifests) == {"AKSNodeClass", "NodePool"}
    assert manifests["NodePool"] == read_yaml(NAP_ROOT / "node-pool.yaml")
    local_dns = manifests["AKSNodeClass"]["spec"]["localDNS"]
    assert local_dns.pop("mode") == mode
    arm = json.loads(
        (REPO_ROOT / "infra/modules/templates/aks-localdns.json").read_text(
            encoding="utf-8"
        )
    )
    assert arm.pop("mode") == "Required"
    assert set(local_dns) == set(arm)
    for group, overrides in local_dns.items():
        normalized = {}
        for override in overrides:
            item = dict(override)
            zone = item.pop("zone")
            for key in ("cacheDuration", "serveStaleDuration"):
                item[f"{key}InSeconds"] = int(item.pop(key).removesuffix("s"))
            normalized[zone] = item
        assert len(normalized) == len(overrides)
        assert normalized == arm[group]


def test_localdns_preserves_dns_restrictions() -> None:
    policy = read_yaml(
        REPO_ROOT / "k8s/apps/chaos-app/ciliumnetworkpolicy-egress-allowlist.yaml"
    )
    egress = policy["spec"]["egress"]
    host_rules = [rule for rule in egress if "host" in rule.get("toEntities", [])]
    core_dns = next(
        rule
        for rule in egress
        if any(
            endpoint.get("matchLabels", {}).get("k8s:k8s-app") == "kube-dns"
            for endpoint in rule.get("toEndpoints", [])
        )
    )
    assert len(host_rules) == 1
    ports = host_rules[0]["toPorts"]
    assert ports == core_dns["toPorts"]
    assert len(ports) == 1
    assert ports[0]["rules"]["dns"]
    assert {(port["port"], port["protocol"]) for port in ports[0]["ports"]} == {
        ("53", "TCP"),
        ("53", "UDP"),
    }


def test_localdns_collector_is_wired_to_nodes() -> None:
    directory = REPO_ROOT / "k8s/observability"
    filename = "ama-metrics-prometheus-config-node.yaml"
    config = yaml.safe_load(
        read_yaml(directory / filename)["data"]["prometheus-config"]
    )
    job = next(
        job for job in config["scrape_configs"] if job["job_name"] == "localdns-metrics"
    )
    assert job["static_configs"] == [{"targets": ["$NODE_IP:9253"]}]
    assert any(
        rule.get("target_label") == "instance"
        and rule.get("replacement") == "$NODE_NAME"
        for rule in job["relabel_configs"]
    )
    assert filename in read_yaml(directory / "kustomization.yaml")["resources"]
