from __future__ import annotations

import hashlib
import os
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlparse

PUBLIC_PACKAGE_HOSTS = frozenset(("files.pythonhosted.org", "pypi.org"))
UNSAFE_UV_ENVIRONMENT_VARIABLES = frozenset(
    (
        "UV_ALL_EXTRAS",
        "UV_ALL_GROUPS",
        "UV_BUILD_CONSTRAINT",
        "UV_CONSTRAINT",
        "UV_DEFAULT_INDEX",
        "UV_DEFAULT_GROUPS",
        "UV_DEV",
        "UV_EXTRA_INDEX_URL",
        "UV_EXTRA",
        "UV_FIND_LINKS",
        "UV_GROUP",
        "UV_INDEX",
        "UV_INDEX_STRATEGY",
        "UV_INDEX_URL",
        "UV_INSECURE_HOST",
        "UV_ISOLATED",
        "UV_NO_ALL_EXTRAS",
        "UV_NO_CONFIG",
        "UV_NO_DEV",
        "UV_NO_EXTRA",
        "UV_NO_GROUP",
        "UV_NO_INDEX",
        "UV_NO_PROJECT",
        "UV_NO_SOURCES",
        "UV_NO_VERIFY_HASHES",
        "UV_ONLY_GROUP",
        "UV_OVERRIDE",
        "UV_PACKAGE",
        "UV_PROJECT",
        "UV_WORKING_DIR",
    )
)
LEGACY_INDEX_KEYS = frozenset(
    (
        "default-index",
        "allow-insecure-host",
        "extra-index-url",
        "find-links",
        "index-url",
        "index-strategy",
        "insecure-host",
        "no-index",
        "no-verify-hashes",
    )
)


class ApprovedIndexConfigError(ValueError):
    pass


def user_uv_config_path(environ: Mapping[str, str] = os.environ) -> Path:
    configured = environ.get("UV_CONFIG_FILE")
    if configured:
        return Path(configured).expanduser().resolve()

    xdg_config_home = environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser().resolve() / "uv" / "uv.toml"

    if os.name == "nt":
        app_data = environ.get("APPDATA")
        if not app_data:
            raise ApprovedIndexConfigError(
                "APPDATA is not set, so the user-level uv configuration cannot be located."
            )
        return Path(app_data).expanduser().resolve() / "uv" / "uv.toml"

    return Path.home() / ".config" / "uv" / "uv.toml"


def validate_approved_index_config(
    config_path: Path,
    environ: Mapping[str, str] = os.environ,
) -> None:
    overrides = sorted(
        name for name in UNSAFE_UV_ENVIRONMENT_VARIABLES if environ.get(name)
    )
    if overrides:
        raise ApprovedIndexConfigError(
            "Unsafe uv environment overrides are not allowed: " + ", ".join(overrides)
        )

    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ApprovedIndexConfigError(
            "The user-level uv configuration was not found."
        ) from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ApprovedIndexConfigError(
            f"The user-level uv configuration is invalid: {error}"
        ) from error

    conflicting_keys = sorted(LEGACY_INDEX_KEYS.intersection(config))
    if conflicting_keys:
        raise ApprovedIndexConfigError(
            "Unsupported uv source or verification settings are present: "
            + ", ".join(conflicting_keys)
        )
    if "pip" in config:
        raise ApprovedIndexConfigError(
            "A [pip] table is not allowed in the approved-index uv configuration."
        )

    indexes = config.get("index")
    if not isinstance(indexes, list) or len(indexes) != 1:
        raise ApprovedIndexConfigError(
            "The user-level uv configuration must contain exactly one [[index]] entry."
        )

    index = indexes[0]
    if not isinstance(index, dict):
        raise ApprovedIndexConfigError("The [[index]] entry must be a TOML table.")
    if not isinstance(index.get("name"), str) or not index["name"].strip():
        raise ApprovedIndexConfigError(
            "The [[index]] entry must have a non-empty name."
        )
    if index.get("default") is not True:
        raise ApprovedIndexConfigError(
            "The configured [[index]] must set default = true to disable public fallback."
        )

    url = index.get("url")
    if not isinstance(url, str):
        raise ApprovedIndexConfigError("The configured [[index]] must have a URL.")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ApprovedIndexConfigError(
            "The configured [[index]] URL must be an HTTPS URL without "
            "credentials, a query, or a fragment."
        )
    if parsed.hostname.rstrip(".").lower() in PUBLIC_PACKAGE_HOSTS:
        raise ApprovedIndexConfigError(
            "The configured [[index]] must not resolve directly to a public package host."
        )


def config_sha256(config_path: Path) -> str:
    digest = hashlib.sha256()
    with config_path.open("rb") as config_file:
        for chunk in iter(lambda: config_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Sequence[str]) -> int:
    digest_requested = len(argv) == 2 and argv[0] == "digest"
    if len(argv) != 1 and not digest_requested:
        print(
            "Usage: approved_index_config.py [digest] <uv-config-path>",
            file=sys.stderr,
        )
        return 2
    config_path = Path(argv[-1])
    try:
        validate_approved_index_config(config_path)
    except ApprovedIndexConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if digest_requested:
        print(config_sha256(config_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
