from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def network_log_manifest() -> dict:
    return yaml.safe_load(
        (REPO_ROOT / "k8s/observability/container-network-log.yaml").read_text(
            encoding="utf-8"
        )
    )


def test_network_log_is_cluster_scoped_with_only_two_directions() -> None:
    manifest = network_log_manifest()
    assert manifest["apiVersion"] == "acn.azure.com/v1alpha1"
    assert manifest["kind"] == "ContainerNetworkLog"
    assert "namespace" not in manifest["metadata"]
    filters = manifest["spec"]["includefilters"]
    assert len(filters) == 2
    assert {item["name"] for item in filters} == {
        "chaos-app-egress",
        "chaos-app-ingress",
    }


@pytest.mark.parametrize(
    ("name", "endpoint"),
    [("chaos-app-egress", "from"), ("chaos-app-ingress", "to")],
)
def test_namespace_and_app_use_distinct_endpoint_predicates(
    name: str, endpoint: str
) -> None:
    # This guards the CRD shape, not the managed operator's runtime translation.
    # Validate generated *_pod AND *_label filters and traffic as in observability.md.
    manifest = network_log_manifest()
    filters = manifest["spec"]["includefilters"]
    selected = next(item for item in filters if item["name"] == name)
    assert selected == {
        "name": name,
        endpoint: {
            "namespacedPod": ["chaos-lab/"],
            "labelSelector": {
                "matchLabels": {
                    "app": "chaos-app",
                }
            }
        },
        "protocol": ["tcp", "udp", "dns"],
        "verdict": ["forwarded", "dropped"],
    }
