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
from typing import Any


def enabled() -> bool:
    for name in (
        "AZD_ALPHA_ENABLE_DEPLOYMENT.STACKS",
        "AZD_ALPHA_ENABLE_DEPLOYMENT_STACKS",
    ):
        value = os.environ.get(name)
        if value is not None:
            if value.lower() in {"true", "1", "t"}:
                return True
            if value.lower() in {"false", "0", "f"}:
                return False
            raise RuntimeError(f"{name} must be true or false")
    executable = shutil.which("azd")
    if not executable:
        raise RuntimeError("azd is required")
    result = subprocess.run(
        [executable, "config", "show"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"azd config show failed: {result.stderr.strip()}")
    try:
        config: Any = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("azd config show returned invalid JSON") from error
    if not isinstance(config, dict) or not isinstance(config.get("alpha", {}), dict):
        raise RuntimeError("invalid azd alpha configuration")
    alpha = config.get("alpha", {})
    return alpha.get("all") == "on" or (
        isinstance(alpha.get("deployment"), dict)
        and alpha["deployment"].get("stacks") == "on"
    )


def main() -> int:
    try:
        if enabled():
            return 0
        raise RuntimeError(
            "Deployment Stacks is disabled. Run `azd config set alpha.deployment.stacks on` "
            "or set AZD_ALPHA_ENABLE_DEPLOYMENT_STACKS=true before provisioning."
        )
    except (RuntimeError, OSError) as error:
        print(f"[deployment-stacks] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
