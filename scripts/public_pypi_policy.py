from __future__ import annotations

from collections.abc import Mapping

DISALLOW_PUBLIC_PYPI_VARIABLE = "AKS_CHAOS_LAB_DISALLOW_PUBLIC_PYPI"
REMOVED_POLICY_VARIABLE = "AZD_ALLOW_PUBLIC_SOURCES"


class PublicPypiPolicyError(ValueError):
    pass


def public_pypi_disallowed(environ: Mapping[str, str]) -> bool:
    if REMOVED_POLICY_VARIABLE in environ:
        raise PublicPypiPolicyError(
            f"{REMOVED_POLICY_VARIABLE} is no longer supported. Remove it and, "
            f"if public PyPI is disallowed, set {DISALLOW_PUBLIC_PYPI_VARIABLE}=true. "
            "See docs/deployment.md#docker-build-のpackage-index."
        )
    value = environ.get(DISALLOW_PUBLIC_PYPI_VARIABLE, "false").strip().lower()
    if value not in ("true", "false"):
        raise PublicPypiPolicyError(
            f"{DISALLOW_PUBLIC_PYPI_VARIABLE} must be true or false."
        )
    return value == "true"
