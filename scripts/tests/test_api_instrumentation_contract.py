import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONNECTION_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"


@pytest.mark.parametrize("service_name", ["api", "api-instrumentation"])
def test_azd_connection_string_is_only_passed_to_instrumentation(
    tmp_path: Path, service_name: str
) -> None:
    if executable := shutil.which("kustomize"):
        command = [executable, "build"]
    elif executable := shutil.which("kubectl"):
        command = [executable, "kustomize"]
    else:
        pytest.skip("kustomize or kubectl is required")

    services = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text(encoding="utf-8"))[
        "services"
    ]
    service = services[service_name]
    config = service["k8s"]["kustomize"]
    source = (REPO_ROOT / service["project"] / config["dir"]).resolve()
    shutil.copytree(
        REPO_ROOT / "k8s",
        tmp_path / "k8s",
        ignore=shutil.ignore_patterns(".env"),
    )
    directory = tmp_path / source.relative_to(REPO_ROOT)
    values = {key: f"test-{key.lower()}" for key in config["env"]}
    (directory / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    result = subprocess.run(
        [*command, str(directory)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    manifests = {
        (item["kind"], item["metadata"]["name"]): item
        for item in yaml.safe_load_all(result.stdout)
    }

    if service_name == "api":
        assert CONNECTION_ENV not in config["env"]
        app_config = manifests["ConfigMap", "app-config"]["data"]
        assert CONNECTION_ENV not in app_config
        assert all(app_config[key] == value for key, value in values.items())
        pod = manifests["Deployment", "chaos-app"]["spec"]["template"]
        assert (
            pod["metadata"]["annotations"][
                "instrumentation.opentelemetry.io/inject-configuration"
            ]
            == "chaos-app-otel"
        )
        for container in pod["spec"]["containers"]:
            assert all(
                item["name"] != CONNECTION_ENV for item in container.get("env", [])
            )
            assert container["envFrom"] == [{"configMapRef": {"name": "app-config"}}]
    else:
        assert config["env"][CONNECTION_ENV] == f"${{{CONNECTION_ENV}}}"
        instrumentation = manifests["Instrumentation", "chaos-app-otel"]
        assert (
            instrumentation["spec"]["destination"][
                "applicationInsightsConnectionString"
            ]
            == values[CONNECTION_ENV]
        )
