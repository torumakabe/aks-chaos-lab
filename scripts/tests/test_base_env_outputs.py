from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("name", "parameter", "output_type"),
    [
        ("AZURE_AKS_NODE_VM_SIZE", "nodeVmSize", "string"),
        (
            "AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING",
            "enableNodeAutoProvisioning",
            "bool",
        ),
        ("AKS_SKU_NAME", "aksSkuName", "string"),
        ("AZURE_AKS_SAFEGUARDS_LEVEL", "aksSafeguardsLevel", "string"),
        ("AZURE_SRE_AGENT_ENABLED", "enableSreAgent", "bool"),
        (
            "AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED",
            "enableSreAgentAzureMonitorIncidentIntegration",
            "bool",
        ),
        ("AZURE_SRE_AGENT_SUBNET_PREFIX", "sreAgentSubnetPrefix", "string"),
        ("AZURE_SRE_AGENT_MONTHLY_AAU_LIMIT", "sreAgentMonthlyAgentUnitLimit", "int"),
        ("AZURE_SRE_AGENT_MODEL_PROVIDER", "sreAgentModelProvider", "string"),
        ("AZURE_SRE_AGENT_MODEL_NAME", "sreAgentModelName", "string"),
        (
            "AZURE_MONITOR_SLI_PARENT_SERVICE_GROUP_ID",
            "azureMonitorSliParentServiceGroupId",
            "string",
        ),
        (
            "AZURE_MONITOR_SLI_SERVICE_GROUP_RESOURCE_ID",
            "azureMonitorSliServiceGroupResourceId",
            "string",
        ),
    ],
)
def test_base_environment_input_is_returned_as_output(
    name: str, parameter: str, output_type: str
) -> None:
    params = json.loads((ROOT / "infra/main.parameters.json").read_text())
    assert params["parameters"][parameter]["value"].startswith(f"${{{name}:")
    source = (ROOT / "infra/main.bicep").read_text()
    assert re.search(
        rf"^output {re.escape(name)} {output_type} = {re.escape(parameter)}$",
        source,
        re.MULTILINE,
    )
