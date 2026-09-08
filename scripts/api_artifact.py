"""専用 API build の成果物と、公開先・稼働 container の同一性を確認する。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aks_connection import AKSConnectionError, Deadline, run_command

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_LOCAL_REF = re.compile(r"aks-chaos-lab-approved:sha256-([0-9a-f]{64})")
_REMOTE_REF = re.compile(
    r"[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[0-9a-f]{64})"
)
_MANIFEST_TYPES = {
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
}
_INDEX_TYPES = {
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
}
_CONFIG_TYPES = {
    "application/vnd.docker.container.image.v1+json",
    "application/vnd.oci.image.config.v1+json",
}


class ApiArtifactError(RuntimeError):
    """成果物を特定できない、または同一性を証明できない。"""


def _docker(
    args: list[str],
    *,
    timeout: int = 120,
    cwd: Path | None = None,
    deadline: Deadline | None = None,
) -> str:
    if deadline is not None:
        try:
            return run_command(
                ["docker", *args],
                deadline=deadline,
                env=os.environ,
                operation="Inspect published API image",
            )
        except AKSConnectionError as exc:
            raise ApiArtifactError(str(exc)) from None
    executable = shutil.which("docker")
    if executable is None:
        raise ApiArtifactError("docker executable is unavailable")
    try:
        result = subprocess.run(
            [executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ApiArtifactError(f"docker {args[0]} timed out") from None
    except subprocess.CalledProcessError as exc:
        # Build output can contain package-index URLs and credentials.
        raise ApiArtifactError(
            f"docker {args[0]} failed (exit {exc.returncode}); output withheld"
        ) from None
    except OSError:
        raise ApiArtifactError(f"docker {args[0]} could not start") from None
    return result.stdout


def _json_output(args: list[str], *, deadline: Deadline | None = None) -> Any:
    try:
        return json.loads(_docker(args, deadline=deadline))
    except ValueError, TypeError:
        raise ApiArtifactError("docker returned invalid JSON") from None


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiArtifactError(f"{label}: expected an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ApiArtifactError(f"{label}: expected an array")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ApiArtifactError("unsupported or missing sha256 digest")
    return value


def build_api_image(root: Path, config_path: Path, config_sha: str) -> str:
    """検証済みの approved-index 設定で build し、成功後だけ専用 tag を発行する。"""
    root = root.resolve()
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ApiArtifactError("approved-index configuration file is missing")
    if re.fullmatch(r"[0-9a-f]{64}", config_sha) is None:
        raise ApiArtifactError("invalid approved-index configuration hash")
    scratch = root / "tmp"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="api-build-", dir=scratch) as directory:
        iidfile = Path(directory) / "image-id"
        _docker(
            [
                "build",
                "--platform",
                "linux/arm64",
                "--file",
                str(root / "src/api/Dockerfile"),
                "--build-arg",
                "UV_INDEX_MODE=approved-index",
                "--build-arg",
                f"UV_INDEX_CONFIG_SHA256={config_sha}",
                "--secret",
                f"id=uv-config,src={config_path}",
                "--iidfile",
                str(iidfile),
                str(root),
            ],
            timeout=1800,
            cwd=root,
        )
        try:
            image_id = _digest(iidfile.read_text(encoding="utf-8").strip())
        except OSError:
            raise ApiArtifactError(
                "successful build did not produce an image ID"
            ) from None
        _inspect_image(image_id, image_id)
        reference = f"aks-chaos-lab-approved:sha256-{image_id.removeprefix('sha256:')}"
        _docker(["tag", image_id, reference])
    return reference


def _inspect_image(reference: str, expected_id: str) -> str:
    images = _list(_json_output(["image", "inspect", reference]), "local images")
    if len(images) != 1:
        raise ApiArtifactError("expected exactly one local image")
    image = _object(images[0], "local image")
    if _digest(image.get("Id")) != expected_id:
        raise ApiArtifactError("local image ID does not match the supplied reference")
    if image.get("Os") != "linux" or image.get("Architecture") != "arm64":
        raise ApiArtifactError("local image must be linux/arm64")
    return expected_id


def inspect_local_image(reference: str) -> str:
    """専用 tag の形式、存在、image ID、linux/arm64 を Azure 操作前に検証する。"""
    match = _LOCAL_REF.fullmatch(reference)
    if match is None:
        raise ApiArtifactError("expected an explicit approved API image reference")
    return _inspect_image(reference, f"sha256:{match[1]}")


def _manifest_config(manifest: Any) -> str:
    manifest = _object(manifest, "manifest")
    if manifest.get("schemaVersion") != 2:
        raise ApiArtifactError("unsupported manifest schema")
    if manifest.get("mediaType") not in _MANIFEST_TYPES:
        raise ApiArtifactError("unsupported manifest media type")
    config = _object(manifest.get("config"), "manifest config")
    if config.get("mediaType") not in _CONFIG_TYPES:
        raise ApiArtifactError("unsupported image config media type")
    return _digest(config.get("digest"))


@dataclass(frozen=True)
class PublishedApiImage:
    """ローカル image ID と照合済みの config、arm64 manifest、index。"""

    remote_ref: str
    config_digest: str
    manifest_digest: str
    index_digest: str | None = None


def _repository(reference: str) -> str:
    repository = reference.split("@", 1)[0]
    if ":" in repository.rsplit("/", 1)[-1]:
        repository = repository.rsplit(":", 1)[0]
    return repository


def inspect_published_image(
    local_id: str, remote_ref: str, deadline: Deadline | None = None
) -> PublishedApiImage:
    """Docker の既存 registry 認証で単一 manifest / attestation 付き index を照合する。"""
    _digest(local_id)
    if _REMOTE_REF.fullmatch(remote_ref) is None:
        raise ApiArtifactError("invalid fully qualified published image reference")
    manifest = _object(
        _json_output(
            [
                "buildx",
                "imagetools",
                "inspect",
                remote_ref,
                "--format",
                "{{json .Manifest}}",
            ],
            deadline=deadline,
        ),
        "published manifest",
    )
    root_digest = _digest(manifest.get("digest"))
    if "@" in remote_ref and root_digest != remote_ref.split("@", 1)[1]:
        raise ApiArtifactError("published root digest differs from pinned reference")
    expected_digest = root_digest
    expected_type = manifest.get("mediaType")
    index_digest = None
    if manifest.get("mediaType") in _INDEX_TYPES:
        if manifest.get("schemaVersion") != 2:
            raise ApiArtifactError("unsupported published index schema")
        index_digest = root_digest
        descriptors = _list(manifest.get("manifests"), "index manifests")
        candidates = []
        attestations = []
        seen_digests: set[str] = set()
        for item in descriptors:
            item = _object(item, "index descriptor")
            child_digest = _digest(item.get("digest"))
            if child_digest in seen_digests or child_digest == root_digest:
                raise ApiArtifactError("duplicate or recursive index child digest")
            seen_digests.add(child_digest)
            if item.get("mediaType") not in _MANIFEST_TYPES:
                raise ApiArtifactError("unsupported index child media type")
            platform = _object(item.get("platform"), "index platform")
            if (
                platform.get("os") == "linux"
                and platform.get("architecture") == "arm64"
                and platform.get("variant") in (None, "", "v8")
            ):
                candidates.append(item)
            elif (
                platform.get("os") == "unknown"
                and platform.get("architecture") == "unknown"
            ):
                attestations.append(item)
            else:
                raise ApiArtifactError("unsupported index platform")
        if len(candidates) != 1:
            raise ApiArtifactError(
                "index must contain exactly one linux/arm64 manifest"
            )
        expected_digest = candidates[0]["digest"]
        expected_type = candidates[0]["mediaType"]
        for item in attestations:
            annotations = _object(item.get("annotations"), "attestation annotations")
            if (
                annotations.get("vnd.docker.reference.type") != "attestation-manifest"
                or annotations.get("vnd.docker.reference.digest") != expected_digest
            ):
                raise ApiArtifactError("unrecognized attestation descriptor")
    elif manifest.get("mediaType") not in _MANIFEST_TYPES:
        raise ApiArtifactError("unsupported published manifest media type")

    # .Manifest は attestation annotations を保持する。以降は tag を再解決しない。
    manifest_ref = f"{_repository(remote_ref)}@{expected_digest}"
    inspected = _object(
        _json_output(
            ["manifest", "inspect", "--verbose", manifest_ref], deadline=deadline
        ),
        "verbose manifest",
    )
    descriptor = _object(inspected.get("Descriptor"), "manifest descriptor")
    if descriptor.get("mediaType") != expected_type:
        raise ApiArtifactError(
            "verbose manifest media type differs from registry descriptor"
        )
    manifest_digest = _digest(descriptor.get("digest"))
    if manifest_digest != expected_digest:
        raise ApiArtifactError(
            "published manifest digest differs from registry descriptor"
        )
    platform = _object(descriptor.get("platform"), "published platform")
    if (
        platform.get("os") != "linux"
        or platform.get("architecture") != "arm64"
        or platform.get("variant") not in (None, "", "v8")
    ):
        raise ApiArtifactError("published image must be linux/arm64")
    payloads = [
        inspected[key]
        for key in ("SchemaV2Manifest", "OCIManifest")
        if inspected.get(key) is not None
    ]
    if len(payloads) != 1:
        raise ApiArtifactError("expected exactly one published manifest payload")
    config_digest = _manifest_config(payloads[0])
    if payloads[0]["mediaType"] != expected_type:
        raise ApiArtifactError(
            "manifest payload media type differs from registry descriptor"
        )
    if local_id not in (config_digest, manifest_digest, index_digest):
        raise ApiArtifactError("published image digests differ from local image ID")
    return PublishedApiImage(remote_ref, config_digest, manifest_digest, index_digest)


def _app(containers: Any) -> dict[str, Any]:
    entries = _list(containers, "containers")
    matching = [
        _object(entry, "container")
        for entry in entries
        if _object(entry, "container").get("name") == "app"
    ]
    if len(matching) != 1:
        raise ApiArtifactError("expected exactly one app container")
    return matching[0]


def _controller(metadata: dict[str, Any], kind: str, uid: str) -> bool:
    owners = _list(metadata.get("ownerReferences", []), "owner references")
    controllers = [
        _object(owner, "owner")
        for owner in owners
        if _object(owner, "owner").get("controller") is True
    ]
    return (
        len(controllers) == 1
        and controllers[0].get("kind") == kind
        and controllers[0].get("uid") == uid
    )


def _runtime_matches(image_id: Any, published: PublishedApiImage) -> bool:
    if not isinstance(image_id, str):
        return False
    if image_id.startswith("docker-pullable://"):
        image_id = image_id.removeprefix("docker-pullable://")
        if "@" not in image_id:
            return False
    elif image_id.startswith(("containerd://", "docker://")):
        image_id = image_id.split("://", 1)[1]
    if "@" in image_id:
        return any(
            image_id == f"{_repository(published.remote_ref)}@{digest}"
            for digest in (published.manifest_digest, published.index_digest)
            if digest is not None
        )
    return image_id in (
        published.config_digest,
        published.manifest_digest,
        published.index_digest,
    )


def verify_running_api(
    published: PublishedApiImage,
    deployment: dict[str, Any],
    pods: dict[str, Any],
    replicasets: dict[str, Any],
) -> None:
    """rollout 後の snapshot を検証。終了中の旧 Pod は除外し、現行 Pod は全件照合する。"""
    deployment = _object(deployment, "Deployment")
    if deployment.get("kind") != "Deployment":
        raise ApiArtifactError("expected a Deployment")
    metadata = _object(deployment.get("metadata"), "Deployment metadata")
    uid, namespace = metadata.get("uid"), metadata.get("namespace")
    if (
        not isinstance(uid, str)
        or not uid
        or not isinstance(namespace, str)
        or not namespace
    ):
        raise ApiArtifactError("Deployment UID and namespace are required")
    if metadata.get("deletionTimestamp"):
        raise ApiArtifactError("Deployment is terminating")
    spec = _object(deployment.get("spec"), "Deployment spec")
    replicas = spec.get("replicas")
    if type(replicas) is not int or replicas <= 0:
        raise ApiArtifactError("Deployment must request positive replicas")
    template = _object(spec.get("template"), "Deployment template")
    template_spec = _object(template.get("spec"), "Deployment template spec")
    if _app(template_spec.get("containers")).get("image") != published.remote_ref:
        raise ApiArtifactError("Deployment app image differs from published reference")
    status = _object(deployment.get("status"), "Deployment status")
    generation = metadata.get("generation")
    observed = status.get("observedGeneration")
    if (
        type(generation) is not int
        or type(observed) is not int
        or observed < generation
        or any(
            type(status.get(key)) is not int or status.get(key) != replicas
            for key in (
                "replicas",
                "updatedReplicas",
                "readyReplicas",
                "availableReplicas",
            )
        )
        or status.get("unavailableReplicas", 0) != 0
    ):
        raise ApiArtifactError("Deployment rollout is incomplete")
    conditions = _list(status.get("conditions"), "Deployment conditions")
    if not any(
        _object(c, "condition").get("type") == "Available" and c.get("status") == "True"
        for c in conditions
    ) or not any(
        _object(c, "condition").get("type") == "Progressing"
        and c.get("status") == "True"
        and c.get("reason") == "NewReplicaSetAvailable"
        for c in conditions
    ):
        raise ApiArtifactError("Deployment availability/progress is not confirmed")
    revision = _object(metadata.get("annotations"), "Deployment annotations").get(
        "deployment.kubernetes.io/revision"
    )
    if not isinstance(revision, str) or not revision:
        raise ApiArtifactError("Deployment revision is missing")
    owned: dict[str, bool] = {}
    for item in _list(
        _object(replicasets, "ReplicaSet list").get("items"), "ReplicaSets"
    ):
        item = _object(item, "ReplicaSet")
        meta = _object(item.get("metadata"), "ReplicaSet metadata")
        if meta.get("namespace") != namespace or not _controller(
            meta, "Deployment", uid
        ):
            continue
        rs_uid = meta.get("uid")
        if (
            item.get("kind") != "ReplicaSet"
            or not isinstance(rs_uid, str)
            or not rs_uid
        ):
            raise ApiArtifactError("invalid owned ReplicaSet")
        annotations = _object(meta.get("annotations"), "ReplicaSet annotations")
        current = annotations.get("deployment.kubernetes.io/revision") == revision
        if current:
            if meta.get("deletionTimestamp"):
                raise ApiArtifactError("current ReplicaSet is terminating")
            rs_spec = _object(item.get("spec"), "ReplicaSet spec")
            rs_template = _object(rs_spec.get("template"), "ReplicaSet template")
            rs_template_spec = _object(
                rs_template.get("spec"), "ReplicaSet template spec"
            )
            if (
                _app(rs_template_spec.get("containers")).get("image")
                != published.remote_ref
            ):
                raise ApiArtifactError("current ReplicaSet image differs")
        if rs_uid in owned:
            raise ApiArtifactError("duplicate ReplicaSet UID")
        owned[rs_uid] = current
    if sum(owned.values()) != 1:
        raise ApiArtifactError("expected exactly one current owned ReplicaSet")
    active = 0
    seen: set[str] = set()
    for pod in _list(_object(pods, "Pod list").get("items"), "Pods"):
        pod = _object(pod, "Pod")
        meta = _object(pod.get("metadata"), "Pod metadata")
        if meta.get("namespace") != namespace:
            continue
        owners = [rs_uid for rs_uid in owned if _controller(meta, "ReplicaSet", rs_uid)]
        if not owners:
            continue
        current = owned[owners[0]]
        if meta.get("deletionTimestamp") and not current:
            continue
        if not current or meta.get("deletionTimestamp"):
            raise ApiArtifactError("old active Pod or terminating current Pod remains")
        pod_uid = meta.get("uid")
        if (
            pod.get("kind") != "Pod"
            or not isinstance(pod_uid, str)
            or not pod_uid
            or pod_uid in seen
        ):
            raise ApiArtifactError("invalid or duplicate owned Pod UID")
        seen.add(pod_uid)
        pod_spec = _object(pod.get("spec"), "Pod spec")
        if _app(pod_spec.get("containers")).get("image") != published.remote_ref:
            raise ApiArtifactError("Pod app image differs from published reference")
        pod_status = _object(pod.get("status"), "Pod status")
        ready = any(
            _object(c, "Pod condition").get("type") == "Ready"
            and c.get("status") == "True"
            for c in _list(pod_status.get("conditions"), "Pod conditions")
        )
        app = _app(pod_status.get("containerStatuses"))
        state = _object(app.get("state"), "app container state")
        if (
            pod_status.get("phase") != "Running"
            or not ready
            or app.get("ready") is not True
            or not isinstance(state.get("running"), dict)
        ):
            raise ApiArtifactError("Pod app container is not running and Ready")
        if not _runtime_matches(app.get("imageID"), published):
            raise ApiArtifactError("Pod runtime image ID is unsupported or mismatched")
        active += 1
    if active != replicas:
        raise ApiArtifactError("ready owned Pod count differs from Deployment replicas")


def verify_api_deployment(
    local_id: str,
    remote_ref: str,
    deployment: dict[str, Any],
    pods: dict[str, Any],
    replicasets: dict[str, Any],
) -> None:
    """公開 manifest を取得し、rollout 後の Deployment → ReplicaSet → Pod を照合する。"""
    published = inspect_published_image(local_id, remote_ref)
    verify_running_api(published, deployment, pods, replicasets)
