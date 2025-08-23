"""Azure Developer CLI environment integration.

Lightweight helper to read values from `azd env` when available,
falling back to standard environment variables.
"""

import os
import shutil
import subprocess
from typing import Any


def get_azd_env_value(key: str, default: Any = None) -> Any:
    """Get environment value from azd if available, otherwise from OS env.

    Returns the resolved value or the provided default.
    """
    azd_path = shutil.which("azd")
    if not azd_path:
        return os.getenv(key, default)

    try:
        result = subprocess.run(  # noqa: S603
            [azd_path, "env", "get-value", key],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return os.getenv(key, default)


def is_azd_available() -> bool:
    azd_path = shutil.which("azd")
    if not azd_path:
        return False
    try:
        result = subprocess.run(  # noqa: S603
            [azd_path, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
