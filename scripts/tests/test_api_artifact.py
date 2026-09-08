from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_artifact as artifact  # noqa: E402

LOCAL_ID = "sha256:" + "a" * 64
MANIFEST_ID = "sha256:" + "b" * 64
OTHER_ID = "sha256:" + "c" * 64
INDEX_ID = "sha256:" + "d" * 64
LOCAL_REF = "aks-chaos-lab-approved:sha256-" + "a" * 64
REMOTE_REF = "example.azurecr.io/api:sha256-" + "a" * 64
REPOSITORY = "example.azurecr.io/api"
MEDIA = "application/vnd.oci.image.manifest.v1+json"
INDEX_MEDIA = "application/vnd.oci.image.index.v1+json"
CONFIG_MEDIA = "application/vnd.oci.image.config.v1+json"


def image() -> dict[str, str]:
    return {"Id": LOCAL_ID, "Os": "linux", "Architecture": "arm64"}


def manifest() -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "mediaType": MEDIA,
        "config": {"mediaType": CONFIG_MEDIA, "digest": LOCAL_ID, "size": 100},
        "layers": [],
    }


def verbose() -> dict[str, Any]:
    return {
        "Descriptor": {
            "digest": MANIFEST_ID,
            "mediaType": MEDIA,
            "platform": {"os": "linux", "architecture": "arm64"},
        },
        "OCIManifest": manifest(),
    }


def registry_manifest() -> dict[str, Any]:
    return {
        "mediaType": MEDIA,
        "digest": MANIFEST_ID,
        "size": 500,
    }


def index() -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "mediaType": INDEX_MEDIA,
        "digest": INDEX_ID,
        "size": 856,
        "manifests": [
            {
                "mediaType": MEDIA,
                "digest": MANIFEST_ID,
                "platform": {"os": "linux", "architecture": "arm64"},
            },
            {
                "mediaType": MEDIA,
                "digest": OTHER_ID,
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": MANIFEST_ID,
                },
            },
        ],
    }


def mock_outputs(
    monkeypatch: pytest.MonkeyPatch, outputs: list[Any]
) -> list[list[str]]:
    calls: list[list[str]] = []

    def docker(args: list[str], **kwargs: Any) -> str:
        calls.append(args)
        assert outputs, f"Unexpected docker command: {args}"
        return json.dumps(outputs.pop(0))

    monkeypatch.setattr(artifact, "_docker", docker)
    return calls


def test_build_flags_and_content_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config with spaces.toml"
    config.write_text("[[index]]", encoding="utf-8")
    calls = []
    iidfiles = []

    def docker(args: list[str], **kwargs: Any) -> str:
        calls.append((args, kwargs))
        if args[0] == "build":
            iidfile = Path(args[args.index("--iidfile") + 1])
            iidfiles.append(iidfile)
            assert iidfile.parent.parent == tmp_path / "tmp"
            assert not iidfile.exists()
            iidfile.write_text(LOCAL_ID + "\n", encoding="utf-8")
        if args[:2] == ["image", "inspect"]:
            return json.dumps([image()])
        return ""

    monkeypatch.setattr(artifact, "_docker", docker)
    assert artifact.build_api_image(tmp_path, config, "d" * 64) == LOCAL_REF
    assert calls[0] == (
        [
            "build",
            "--platform",
            "linux/arm64",
            "--file",
            str(tmp_path / "src/api/Dockerfile"),
            "--build-arg",
            "UV_INDEX_MODE=approved-index",
            "--build-arg",
            "UV_INDEX_CONFIG_SHA256=" + "d" * 64,
            "--secret",
            f"id=uv-config,src={config}",
            "--iidfile",
            str(iidfiles[0]),
            str(tmp_path),
        ],
        {"timeout": 1800, "cwd": tmp_path},
    )
    assert calls[1][0] == ["image", "inspect", LOCAL_ID]
    assert calls[2][0] == ["tag", LOCAL_ID, LOCAL_REF]
    assert "aks-chaos-lab:local" not in str(calls)
    assert not iidfiles[0].exists()
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.parametrize(
    "failure", ["build", "missing-iid", "invalid-iid", "inspect", "tag"]
)
def test_failed_build_never_returns_success_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    config = tmp_path / "config.toml"
    config.touch()
    calls = []

    def docker(args: list[str], **kwargs: Any) -> str:
        calls.append(args)
        if args[0] == "build":
            if failure == "build":
                raise artifact.ApiArtifactError("build failed")
            if failure != "missing-iid":
                Path(args[args.index("--iidfile") + 1]).write_text(
                    "invalid" if failure == "invalid-iid" else LOCAL_ID,
                    encoding="utf-8",
                )
        elif args[:2] == ["image", "inspect"]:
            if failure == "inspect":
                raise artifact.ApiArtifactError("inspect failed")
            return json.dumps([image()])
        elif args[0] == "tag":
            raise artifact.ApiArtifactError("tag failed")
        return ""

    monkeypatch.setattr(artifact, "_docker", docker)
    with pytest.raises(artifact.ApiArtifactError):
        artifact.build_api_image(tmp_path, config, "d" * 64)
    if failure != "tag":
        assert not any(args[0] == "tag" for args in calls)
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.parametrize(
    "reference", ["", "aks-chaos-lab:local", REMOTE_REF, LOCAL_REF[:-1]]
)
def test_invalid_input_fails_without_docker(
    monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    calls = mock_outputs(monkeypatch, [])
    with pytest.raises(artifact.ApiArtifactError):
        artifact.inspect_local_image(reference)
    assert calls == []


@pytest.mark.parametrize(
    "images",
    [
        [],
        [image(), image()],
        [{"Id": OTHER_ID, "Os": "linux", "Architecture": "arm64"}],
        [{"Id": LOCAL_ID, "Os": "linux", "Architecture": "amd64"}],
        [{"Id": LOCAL_ID, "Os": "windows", "Architecture": "arm64"}],
        [{"Id": LOCAL_ID}],
        {"Id": LOCAL_ID},
    ],
)
def test_rejects_missing_mismatched_and_wrong_platform_local_image(
    monkeypatch: pytest.MonkeyPatch, images: Any
) -> None:
    mock_outputs(monkeypatch, [images])
    with pytest.raises(artifact.ApiArtifactError):
        artifact.inspect_local_image(LOCAL_REF)


def test_inspect_local_image(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = mock_outputs(monkeypatch, [[image()]])
    assert artifact.inspect_local_image(LOCAL_REF) == LOCAL_ID
    assert calls == [["image", "inspect", LOCAL_REF]]


@pytest.mark.parametrize("failure", ["missing-config", "invalid-config-hash"])
def test_build_configuration_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    calls = mock_outputs(monkeypatch, [])
    config = tmp_path / "config.toml"
    if failure == "invalid-config-hash":
        config.touch()
    with pytest.raises(artifact.ApiArtifactError):
        artifact.build_api_image(
            tmp_path, config, "invalid" if config.exists() else "d" * 64
        )
    assert calls == []


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "aks-chaos-lab:local",
        "https://user:credential@example.azurecr.io/api:tag",
        "example.azurecr.io/api",
        "--help",
    ],
)
def test_invalid_published_reference_fails_before_docker(
    monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    calls = mock_outputs(monkeypatch, [])
    with pytest.raises(artifact.ApiArtifactError):
        artifact.inspect_published_image(LOCAL_ID, reference)
    assert calls == []


@pytest.mark.parametrize("digest", [MANIFEST_ID, OTHER_ID])
def test_pinned_remote_reference_requires_matching_manifest_digest(
    monkeypatch: pytest.MonkeyPatch, digest: str
) -> None:
    mock_outputs(monkeypatch, [registry_manifest(), verbose()])
    remote = f"{REPOSITORY}@{digest}"
    if digest == MANIFEST_ID:
        assert (
            artifact.inspect_published_image(LOCAL_ID, remote).manifest_digest == digest
        )
    else:
        with pytest.raises(artifact.ApiArtifactError, match="pinned reference"):
            artifact.inspect_published_image(LOCAL_ID, remote)


@pytest.mark.parametrize(
    "kind", ["single", "docker-single", "index", "index-without-attestation"]
)
def test_published_manifest_config_links_to_local_image(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    raw = index() if kind.startswith("index") else registry_manifest()
    detail = verbose()
    if kind == "docker-single":
        raw["mediaType"] = "application/vnd.docker.distribution.manifest.v2+json"
        payload = manifest()
        payload["mediaType"] = raw["mediaType"]
        payload["config"]["mediaType"] = (
            "application/vnd.docker.container.image.v1+json"
        )
        detail["Descriptor"]["mediaType"] = raw["mediaType"]
        detail["SchemaV2Manifest"] = payload
        del detail["OCIManifest"]
    if kind == "index-without-attestation":
        raw["manifests"].pop()
    calls = mock_outputs(monkeypatch, [raw, detail])
    result = artifact.inspect_published_image(LOCAL_ID, REMOTE_REF)
    assert result == artifact.PublishedApiImage(
        REMOTE_REF,
        LOCAL_ID,
        MANIFEST_ID,
        INDEX_ID if kind.startswith("index") else None,
    )
    selected = f"{REPOSITORY}@{MANIFEST_ID}"
    assert calls == [
        [
            "buildx",
            "imagetools",
            "inspect",
            REMOTE_REF,
            "--format",
            "{{json .Manifest}}",
        ],
        ["manifest", "inspect", "--verbose", selected],
    ]


@pytest.mark.parametrize(
    "failure",
    [
        "root-digest-mismatch",
        "root-digest-missing",
        "unknown-schema",
        "unknown-media",
        "unknown-config",
        "verbose-config-mismatch",
        "verbose-digest-missing",
        "verbose-platform-missing",
        "verbose-wrong-platform",
        "verbose-unknown",
        "ambiguous-payload",
    ],
)
def test_single_manifest_fails_closed(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    raw, detail = registry_manifest(), verbose()
    if failure == "root-digest-mismatch":
        raw["digest"] = OTHER_ID
    elif failure == "root-digest-missing":
        raw.pop("digest")
    elif failure == "unknown-schema":
        detail["OCIManifest"]["schemaVersion"] = 1
    elif failure == "unknown-media":
        raw["mediaType"] = "unknown"
    elif failure == "unknown-config":
        detail["OCIManifest"]["config"]["mediaType"] = "unknown"
    elif failure == "verbose-config-mismatch":
        detail["OCIManifest"]["config"]["digest"] = OTHER_ID
    elif failure == "verbose-digest-missing":
        del detail["Descriptor"]["digest"]
    elif failure == "verbose-platform-missing":
        del detail["Descriptor"]["platform"]
    elif failure == "verbose-wrong-platform":
        detail["Descriptor"]["platform"]["architecture"] = "amd64"
    elif failure == "verbose-unknown":
        del detail["OCIManifest"]
    elif failure == "ambiguous-payload":
        detail["SchemaV2Manifest"] = manifest()
    mock_outputs(monkeypatch, [raw, detail])
    with pytest.raises(artifact.ApiArtifactError):
        artifact.inspect_published_image(LOCAL_ID, REMOTE_REF)


@pytest.mark.parametrize(
    "failure",
    [
        "empty",
        "ambiguous",
        "amd64",
        "unknown-platform",
        "unknown-child",
        "unknown-schema",
        "bad-attestation",
        "wrong-attestation-target",
        "missing-attestation-annotations",
        "child-digest-mismatch",
        "child-config-mismatch",
    ],
)
def test_index_rejects_ambiguity_and_unknowns(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    raw, detail = index(), verbose()
    if failure == "empty":
        raw["manifests"] = []
    elif failure == "ambiguous":
        raw["manifests"].append(copy.deepcopy(raw["manifests"][0]))
    elif failure == "amd64":
        raw["manifests"][0]["platform"]["architecture"] = "amd64"
    elif failure == "unknown-platform":
        raw["manifests"][0].pop("platform")
    elif failure == "unknown-child":
        raw["manifests"][0]["mediaType"] = INDEX_MEDIA
    elif failure == "unknown-schema":
        raw["schemaVersion"] = 1
    elif failure == "bad-attestation":
        raw["manifests"][1]["annotations"]["vnd.docker.reference.type"] = "unknown"
    elif failure == "wrong-attestation-target":
        raw["manifests"][1]["annotations"]["vnd.docker.reference.digest"] = LOCAL_ID
    elif failure == "missing-attestation-annotations":
        raw["manifests"][1].pop("annotations")
    elif failure == "child-digest-mismatch":
        detail["Descriptor"]["digest"] = OTHER_ID
    elif failure == "child-config-mismatch":
        detail["OCIManifest"]["config"]["digest"] = OTHER_ID
    mock_outputs(monkeypatch, [raw, detail])
    with pytest.raises(artifact.ApiArtifactError):
        artifact.inspect_published_image(LOCAL_ID, REMOTE_REF)


@pytest.mark.parametrize(
    ("is_index", "local_id"),
    [
        (False, LOCAL_ID),
        (False, MANIFEST_ID),
        (True, LOCAL_ID),
        (True, MANIFEST_ID),
        (True, INDEX_ID),
    ],
)
def test_local_id_binds_to_config_manifest_or_index(
    monkeypatch: pytest.MonkeyPatch, is_index: bool, local_id: str
) -> None:
    mock_outputs(monkeypatch, [index() if is_index else registry_manifest(), verbose()])
    published = artifact.inspect_published_image(local_id, REMOTE_REF)
    assert published.config_digest == LOCAL_ID
    assert published.manifest_digest == MANIFEST_ID
    assert published.index_digest == (INDEX_ID if is_index else None)


def test_single_manifest_descriptor_needs_no_schema_or_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_id = "sha256:301e8e6460b929946bbb2ef235df410b9f0ccb254c207afe38f21a3c4a88c29c"
    config_id = (
        "sha256:7d854f280ddafdfa2198fc5f3155a376709a28c661d91f1f881c1d5915a0b2a9"
    )
    raw = {"mediaType": MEDIA, "digest": child_id, "size": 2957}
    detail = verbose()
    detail["Descriptor"].update(raw)
    detail["OCIManifest"]["config"].update({"digest": config_id, "size": 10337})
    calls = mock_outputs(monkeypatch, [raw, detail])
    remote_ref = f"{REPOSITORY}@{child_id}"
    assert artifact.inspect_published_image(config_id, remote_ref) == (
        artifact.PublishedApiImage(remote_ref, config_id, child_id)
    )
    assert len(calls) == 2
    assert calls[1] == ["manifest", "inspect", "--verbose", remote_ref]


@pytest.mark.parametrize("local_id", [OTHER_ID, "sha256:" + "e" * 64])
def test_attestation_or_unknown_digest_cannot_bind_local_image(
    monkeypatch: pytest.MonkeyPatch, local_id: str
) -> None:
    mock_outputs(monkeypatch, [index(), verbose()])
    with pytest.raises(artifact.ApiArtifactError, match="local image ID"):
        artifact.inspect_published_image(local_id, REMOTE_REF)


@pytest.mark.parametrize("pinned_digest", [INDEX_ID, MANIFEST_ID])
def test_pinned_index_ref_must_match_root_not_child(
    monkeypatch: pytest.MonkeyPatch, pinned_digest: str
) -> None:
    calls = mock_outputs(monkeypatch, [index(), verbose()])
    remote = f"{REPOSITORY}@{pinned_digest}"
    if pinned_digest == INDEX_ID:
        assert (
            artifact.inspect_published_image(INDEX_ID, remote).index_digest == INDEX_ID
        )
        assert calls[1][-1] == f"{REPOSITORY}@{MANIFEST_ID}"
    else:
        with pytest.raises(artifact.ApiArtifactError, match="pinned reference"):
            artifact.inspect_published_image(INDEX_ID, remote)
        assert len(calls) == 1


def test_changed_tag_root_cannot_bind_original_local_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = index()
    raw["digest"] = "sha256:" + "e" * 64
    calls = mock_outputs(monkeypatch, [raw, verbose()])
    with pytest.raises(artifact.ApiArtifactError, match="local image ID"):
        artifact.inspect_published_image(INDEX_ID, REMOTE_REF)
    assert calls[1][-1] == f"{REPOSITORY}@{MANIFEST_ID}"


@pytest.mark.parametrize(
    "failure",
    [
        "missing-root-digest",
        "invalid-root-digest",
        "additional-platform",
        "unknown-attestation",
        "missing-attestation-annotations",
        "duplicate-attestation",
        "recursive-child",
        "missing-config",
        "invalid-config-digest",
        "invalid-config-type",
        "wrong-child-platform",
        "wrong-child-digest",
        "wrong-child-media",
        "wrong-payload-media",
    ],
)
def test_local_index_binding_does_not_skip_child_validation(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    raw, detail = index(), verbose()
    if failure == "missing-root-digest":
        raw.pop("digest")
    elif failure == "invalid-root-digest":
        raw["digest"] = "not-a-digest"
    elif failure == "additional-platform":
        extra = copy.deepcopy(raw["manifests"][0])
        extra["digest"] = "sha256:" + "e" * 64
        extra["platform"]["architecture"] = "amd64"
        raw["manifests"].append(extra)
    elif failure == "unknown-attestation":
        raw["manifests"][1]["annotations"]["vnd.docker.reference.type"] = "unknown"
    elif failure == "missing-attestation-annotations":
        raw["manifests"][1].pop("annotations")
    elif failure == "duplicate-attestation":
        raw["manifests"].append(copy.deepcopy(raw["manifests"][1]))
    elif failure == "recursive-child":
        raw["manifests"][0]["digest"] = INDEX_ID
    elif failure == "missing-config":
        detail["OCIManifest"].pop("config")
    elif failure == "invalid-config-digest":
        detail["OCIManifest"]["config"]["digest"] = None
    elif failure == "invalid-config-type":
        detail["OCIManifest"]["config"]["mediaType"] = "unknown"
    elif failure == "wrong-child-platform":
        detail["Descriptor"]["platform"]["architecture"] = "amd64"
    elif failure == "wrong-child-digest":
        detail["Descriptor"]["digest"] = OTHER_ID
    elif failure == "wrong-child-media":
        detail["Descriptor"]["mediaType"] = INDEX_MEDIA
    elif failure == "wrong-payload-media":
        detail["OCIManifest"]["mediaType"] = (
            "application/vnd.docker.distribution.manifest.v2+json"
        )
    mock_outputs(monkeypatch, [raw, detail])
    with pytest.raises(artifact.ApiArtifactError):
        artifact.inspect_published_image(INDEX_ID, REMOTE_REF)


def owner(kind: str, uid: str) -> list[dict[str, Any]]:
    return [{"kind": kind, "uid": uid, "controller": True}]


@pytest.fixture
def snapshots() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    annotations = {"deployment.kubernetes.io/revision": "2"}
    template = {"spec": {"containers": [{"name": "app", "image": REMOTE_REF}]}}
    deployment = {
        "kind": "Deployment",
        "metadata": {
            "uid": "deployment-uid",
            "namespace": "chaos-app",
            "generation": 2,
            "annotations": dict(annotations),
        },
        "spec": {"replicas": 1, "template": copy.deepcopy(template)},
        "status": {
            "observedGeneration": 2,
            "replicas": 1,
            "updatedReplicas": 1,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "conditions": [
                {"type": "Available", "status": "True"},
                {
                    "type": "Progressing",
                    "status": "True",
                    "reason": "NewReplicaSetAvailable",
                },
            ],
        },
    }
    replicasets = {
        "items": [
            {
                "kind": "ReplicaSet",
                "metadata": {
                    "uid": "rs-uid",
                    "namespace": "chaos-app",
                    "ownerReferences": owner("Deployment", "deployment-uid"),
                    "annotations": dict(annotations),
                },
                "spec": {"template": copy.deepcopy(template)},
            }
        ]
    }
    pods = {
        "items": [
            {
                "kind": "Pod",
                "metadata": {
                    "uid": "pod-uid",
                    "namespace": "chaos-app",
                    "ownerReferences": owner("ReplicaSet", "rs-uid"),
                },
                "spec": copy.deepcopy(template["spec"]),
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [
                        {
                            "name": "app",
                            "ready": True,
                            "state": {"running": {"startedAt": "2026-09-08T01:00:00Z"}},
                            "imageID": f"{REPOSITORY}@{MANIFEST_ID}",
                        }
                    ],
                },
            }
        ]
    }
    return deployment, pods, replicasets


@pytest.mark.parametrize(
    "runtime",
    [
        "repository",
        "pullable",
        "containerd-repository",
        "bare",
        "containerd",
        "unknown",
        "foreign",
    ],
)
def test_real_containerd_index_id_reaches_running_pod(
    monkeypatch: pytest.MonkeyPatch, snapshots: Any, runtime: str
) -> None:
    root_id = "sha256:6dfc2b535dc002d2a5b1348bc3757cbf4de682e54592890e1282fa359189512d"
    child_id = "sha256:301e8e6460b929946bbb2ef235df410b9f0ccb254c207afe38f21a3c4a88c29c"
    config_id = (
        "sha256:7d854f280ddafdfa2198fc5f3155a376709a28c661d91f1f881c1d5915a0b2a9"
    )
    attestation_id = (
        "sha256:ee121cdf48781102621d158b7e93ebf219360b55b78ce12276306c806e93f213"
    )
    raw, detail = index(), verbose()
    raw["digest"] = root_id
    raw["manifests"][0]["digest"] = child_id
    raw["manifests"][1]["digest"] = attestation_id
    raw["manifests"][1]["annotations"]["vnd.docker.reference.digest"] = child_id
    detail["Descriptor"]["digest"] = child_id
    detail["OCIManifest"]["config"].update({"digest": config_id, "size": 10337})
    calls = mock_outputs(
        monkeypatch,
        [[{"Id": root_id, "Os": "linux", "Architecture": "arm64"}], raw, detail],
    )
    local_id = artifact.inspect_local_image(
        f"aks-chaos-lab-approved:sha256-{root_id.removeprefix('sha256:')}"
    )
    published = artifact.inspect_published_image(local_id, REMOTE_REF)
    assert published.config_digest == config_id
    assert published.index_digest == root_id
    assert calls[-1] == ["manifest", "inspect", "--verbose", f"{REPOSITORY}@{child_id}"]
    runtime_ids = {
        "repository": f"{REPOSITORY}@{root_id}",
        "pullable": f"docker-pullable://{REPOSITORY}@{root_id}",
        "containerd-repository": f"containerd://{REPOSITORY}@{root_id}",
        "bare": root_id,
        "containerd": f"containerd://{root_id}",
        "unknown": f"{REPOSITORY}@{OTHER_ID}",
        "foreign": f"other.azurecr.io/api@{root_id}",
    }
    snapshots[1]["items"][0]["status"]["containerStatuses"][0]["imageID"] = runtime_ids[
        runtime
    ]
    if runtime in ("unknown", "foreign"):
        with pytest.raises(artifact.ApiArtifactError, match="runtime image ID"):
            artifact.verify_running_api(published, *snapshots)
    else:
        artifact.verify_running_api(published, *snapshots)


def test_unverified_index_is_not_a_runtime_identity(snapshots: Any) -> None:
    snapshots[1]["items"][0]["status"]["containerStatuses"][0]["imageID"] = (
        f"{REPOSITORY}@{INDEX_ID}"
    )
    with pytest.raises(artifact.ApiArtifactError, match="runtime image ID"):
        artifact.verify_running_api(
            artifact.PublishedApiImage(REMOTE_REF, LOCAL_ID, MANIFEST_ID), *snapshots
        )


@pytest.mark.parametrize(
    "runtime_id",
    [
        LOCAL_ID,
        MANIFEST_ID,
        f"containerd://{LOCAL_ID}",
        f"containerd://{MANIFEST_ID}",
        f"docker://{LOCAL_ID}",
        f"docker-pullable://{REPOSITORY}@{MANIFEST_ID}",
        f"{REPOSITORY}@{MANIFEST_ID}",
    ],
)
def test_running_api_matches_known_runtime_ids(snapshots: Any, runtime_id: str) -> None:
    deployment, pods, replicasets = snapshots
    pods["items"][0]["status"]["containerStatuses"][0]["imageID"] = runtime_id
    artifact.verify_running_api(
        artifact.PublishedApiImage(REMOTE_REF, LOCAL_ID, MANIFEST_ID),
        deployment,
        pods,
        replicasets,
    )


@pytest.mark.parametrize(
    "runtime_id",
    [
        "",
        None,
        OTHER_ID,
        f"containerd://{OTHER_ID}",
        f"cri-o://{LOCAL_ID}",
        f"unknown://{MANIFEST_ID}",
        f"docker-pullable://{LOCAL_ID}",
        f"{REPOSITORY}@{LOCAL_ID}",
        f"other.azurecr.io/api@{MANIFEST_ID}",
        REMOTE_REF,
    ],
)
def test_runtime_unknown_and_mismatch_fail(snapshots: Any, runtime_id: Any) -> None:
    deployment, pods, replicasets = snapshots
    pods["items"][0]["status"]["containerStatuses"][0]["imageID"] = runtime_id
    with pytest.raises(artifact.ApiArtifactError, match="runtime image ID"):
        artifact.verify_running_api(
            artifact.PublishedApiImage(REMOTE_REF, LOCAL_ID, MANIFEST_ID),
            deployment,
            pods,
            replicasets,
        )


@pytest.mark.parametrize(
    ("target", "path", "value"),
    [
        (0, ("kind",), "StatefulSet"),
        (0, ("metadata", "uid"), ""),
        (0, ("metadata", "generation"), None),
        (0, ("metadata", "deletionTimestamp"), "now"),
        (0, ("metadata", "annotations"), {}),
        (0, ("spec", "replicas"), 0),
        (0, ("spec", "replicas"), -1),
        (0, ("spec", "replicas"), True),
        (0, ("spec", "template", "spec", "containers", 0, "image"), LOCAL_REF),
        (0, ("spec", "template", "spec", "containers"), []),
        (0, ("status", "observedGeneration"), 1),
        (0, ("status", "updatedReplicas"), 0),
        (0, ("status", "readyReplicas"), 0),
        (0, ("status", "availableReplicas"), 0),
        (0, ("status", "replicas"), 2),
        (0, ("status", "unavailableReplicas"), 1),
        (0, ("status", "conditions"), []),
        (0, ("status", "conditions", 0, "status"), "False"),
        (0, ("status", "conditions", 1, "reason"), "ProgressDeadlineExceeded"),
        (1, ("items",), []),
        (
            1,
            ("items", 0, "metadata", "ownerReferences"),
            owner("ReplicaSet", "other-rs"),
        ),
        (
            1,
            ("items", 0, "metadata", "ownerReferences"),
            owner("Deployment", "deployment-uid"),
        ),
        (1, ("items", 0, "metadata", "ownerReferences", 0, "controller"), False),
        (1, ("items", 0, "metadata", "namespace"), "other"),
        (1, ("items", 0, "metadata", "deletionTimestamp"), "now"),
        (1, ("items", 0, "spec", "containers", 0, "image"), LOCAL_REF),
        (1, ("items", 0, "spec", "containers", 0, "name"), "sidecar"),
        (1, ("items", 0, "status", "phase"), "Pending"),
        (1, ("items", 0, "status", "conditions", 0, "status"), "False"),
        (1, ("items", 0, "status", "containerStatuses"), []),
        (1, ("items", 0, "status", "containerStatuses", 0, "name"), "sidecar"),
        (1, ("items", 0, "status", "containerStatuses", 0, "ready"), False),
        (1, ("items", 0, "status", "containerStatuses", 0, "state"), {"waiting": {}}),
        (2, ("items",), []),
        (2, ("items", 0, "metadata", "ownerReferences"), owner("Deployment", "other")),
        (2, ("items", 0, "metadata", "annotations"), {}),
        (2, ("items", 0, "metadata", "deletionTimestamp"), "now"),
        (
            2,
            ("items", 0, "spec", "template", "spec", "containers", 0, "image"),
            LOCAL_REF,
        ),
    ],
)
def test_rollout_snapshot_fails_closed(
    snapshots: Any, target: int, path: tuple[Any, ...], value: Any
) -> None:
    node = snapshots[target]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with pytest.raises(artifact.ApiArtifactError):
        artifact.verify_running_api(
            artifact.PublishedApiImage(REMOTE_REF, LOCAL_ID, MANIFEST_ID), *snapshots
        )


@pytest.mark.parametrize("terminating", [True, False])
def test_old_pods_excluded_only_when_terminating(
    snapshots: Any, terminating: bool
) -> None:
    _, pods, replicasets = snapshots
    old_rs = copy.deepcopy(replicasets["items"][0])
    old_rs["metadata"]["uid"] = "old-rs"
    old_rs["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = "1"
    old_rs["spec"]["template"]["spec"]["containers"][0]["image"] = "old:image"
    replicasets["items"].append(old_rs)
    old_pod = copy.deepcopy(pods["items"][0])
    old_pod["metadata"]["uid"] = "old-pod"
    old_pod["metadata"]["ownerReferences"] = owner("ReplicaSet", "old-rs")
    old_pod["status"] = {}
    if terminating:
        old_pod["metadata"]["deletionTimestamp"] = "now"
    pods["items"].append(old_pod)
    published = artifact.PublishedApiImage(REMOTE_REF, LOCAL_ID, MANIFEST_ID)
    if terminating:
        artifact.verify_running_api(published, *snapshots)
    else:
        with pytest.raises(artifact.ApiArtifactError, match="old active Pod"):
            artifact.verify_running_api(published, *snapshots)


def test_foreign_owner_pods_do_not_count(snapshots: Any) -> None:
    _, pods, _ = snapshots
    foreign = copy.deepcopy(pods["items"][0])
    foreign["metadata"]["uid"] = "foreign-pod"
    foreign["metadata"]["ownerReferences"] = owner("ReplicaSet", "foreign-rs")
    foreign["status"] = {}
    pods["items"].append(foreign)
    artifact.verify_running_api(
        artifact.PublishedApiImage(REMOTE_REF, LOCAL_ID, MANIFEST_ID), *snapshots
    )


def test_multiple_ready_replicas_required(snapshots: Any) -> None:
    deployment, pods, _ = snapshots
    deployment["spec"]["replicas"] = 2
    for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"):
        deployment["status"][key] = 2
    published = artifact.PublishedApiImage(REMOTE_REF, LOCAL_ID, MANIFEST_ID)
    with pytest.raises(artifact.ApiArtifactError, match="count"):
        artifact.verify_running_api(published, *snapshots)
    pod = copy.deepcopy(pods["items"][0])
    pod["metadata"]["uid"] = "second-pod"
    pods["items"].append(pod)
    artifact.verify_running_api(published, *snapshots)
    pods["items"].append(copy.deepcopy(pod))
    with pytest.raises(artifact.ApiArtifactError, match="duplicate"):
        artifact.verify_running_api(published, *snapshots)


def test_verification_fetches_remote_evidence(
    monkeypatch: pytest.MonkeyPatch, snapshots: Any
) -> None:
    calls = mock_outputs(monkeypatch, [registry_manifest(), verbose()])
    artifact.verify_api_deployment(LOCAL_ID, REMOTE_REF, *snapshots)
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["missing", "exit", "timeout", "oserror"])
def test_docker_errors_withhold_sensitive_output(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(
        artifact.shutil,
        "which",
        lambda name: None if failure == "missing" else "/docker",
    )
    sensitive = "https://user:credential@index.example/simple"

    def run(args: list[str], **kwargs: Any) -> Any:
        assert kwargs["timeout"] == 17
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is True
        if failure == "exit":
            raise subprocess.CalledProcessError(
                1, args, output=sensitive, stderr=sensitive
            )
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 17, output=sensitive)
        raise OSError(sensitive)

    monkeypatch.setattr(artifact.subprocess, "run", run)
    with pytest.raises(artifact.ApiArtifactError) as caught:
        artifact._docker(["manifest", "inspect", REMOTE_REF], timeout=17)
    assert sensitive not in str(caught.value)
    if failure != "missing":
        assert caught.value.__suppress_context__


def test_invalid_docker_json_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact, "_docker", lambda *args, **kwargs: "not-json")
    with pytest.raises(artifact.ApiArtifactError, match="invalid JSON"):
        artifact.inspect_local_image(LOCAL_REF)


@pytest.mark.parametrize("raw", [registry_manifest(), index()])
def test_registry_commands_share_deadline_and_existing_process_runner(
    monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
) -> None:
    clock = [100.0]
    monkeypatch.setattr("aks_connection.time.monotonic", lambda: clock[0])
    monkeypatch.setenv("DOCKER_CONFIG", "existing-docker-config")
    deadline = artifact.Deadline(15)
    outputs = [raw, verbose()]
    calls = []

    def run_command(args: list[str], **kwargs: Any) -> str:
        assert kwargs["deadline"] is deadline
        assert kwargs["env"]["DOCKER_CONFIG"] == "existing-docker-config"
        assert kwargs["operation"] == "Inspect published API image"
        calls.append((args, deadline.remaining()))
        clock[0] += 6
        return json.dumps(outputs.pop(0))

    monkeypatch.setattr(artifact, "run_command", run_command)
    monkeypatch.setattr(
        artifact.subprocess, "run", lambda *a, **kw: pytest.fail("unbounded runner")
    )
    result = artifact.inspect_published_image(LOCAL_ID, REMOTE_REF, deadline)
    selected = f"{REPOSITORY}@{MANIFEST_ID}"
    assert result.manifest_digest == MANIFEST_ID
    assert calls == [
        (
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                REMOTE_REF,
                "--format",
                "{{json .Manifest}}",
            ],
            15,
        ),
        (["docker", "manifest", "inspect", "--verbose", selected], 9),
    ]
    assert deadline.remaining() == 3


@pytest.mark.parametrize("already_expired", [False, True])
def test_expired_registry_deadline_does_not_start_next_command(
    monkeypatch: pytest.MonkeyPatch, already_expired: bool
) -> None:
    clock = [100.0]
    monkeypatch.setattr("aks_connection.time.monotonic", lambda: clock[0])
    deadline = artifact.Deadline(10)
    if already_expired:
        clock[0] = 110.0
    started = []

    def run_command(args: list[str], **kwargs: Any) -> str:
        kwargs["deadline"].remaining()
        started.append(args)
        clock[0] = 110.0
        return json.dumps(registry_manifest())

    monkeypatch.setattr(artifact, "run_command", run_command)
    with pytest.raises(artifact.ApiArtifactError, match="deadline exceeded"):
        artifact.inspect_published_image(LOCAL_ID, REMOTE_REF, deadline=deadline)
    assert len(started) == (0 if already_expired else 1)


def test_registry_runner_preserves_safe_failure_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_command(*args: Any, **kwargs: Any) -> str:
        raise artifact.AKSConnectionError(
            "Inspect published API image: authentication (exit 1); output withheld",
            category="authentication",
        )

    monkeypatch.setattr(artifact, "run_command", run_command)
    with pytest.raises(artifact.ApiArtifactError, match="authentication") as caught:
        artifact.inspect_published_image(LOCAL_ID, REMOTE_REF, artifact.Deadline(30))
    assert caught.value.__suppress_context__
