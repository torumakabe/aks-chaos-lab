"""Tests for azd_env module."""

import subprocess
from unittest.mock import MagicMock, patch

from app.azd_env import get_azd_env_value, is_azd_available


class TestGetAzdEnvValue:
    """Tests for get_azd_env_value function."""

    @patch("app.azd_env.shutil.which", return_value=None)
    def test_returns_env_var_when_azd_not_installed(
        self, mock_which: MagicMock
    ) -> None:
        """Fall back to os.getenv when azd is not installed."""
        with patch.dict("os.environ", {"MY_KEY": "from-env"}):
            assert get_azd_env_value("MY_KEY") == "from-env"

    @patch("app.azd_env.shutil.which", return_value=None)
    def test_returns_default_when_no_azd_and_no_env(
        self, mock_which: MagicMock
    ) -> None:
        """Return default when azd missing and env var not set."""
        with patch.dict("os.environ", {}, clear=True):
            assert get_azd_env_value("MISSING", "fallback") == "fallback"

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch("app.azd_env.subprocess.run")
    def test_returns_azd_value_on_success(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Return stripped stdout when azd succeeds."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="azd-value\n"
        )
        assert get_azd_env_value("KEY") == "azd-value"

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch("app.azd_env.subprocess.run")
    def test_falls_back_to_env_on_azd_failure(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Fall back to os.getenv when azd returns non-zero."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=""
        )
        with patch.dict("os.environ", {"KEY": "env-val"}):
            assert get_azd_env_value("KEY") == "env-val"

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch("app.azd_env.subprocess.run")
    def test_falls_back_on_empty_stdout(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Fall back when azd succeeds but stdout is empty."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=""
        )
        with patch.dict("os.environ", {"KEY": "env-val"}):
            assert get_azd_env_value("KEY") == "env-val"

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch(
        "app.azd_env.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="azd", timeout=10),
    )
    def test_falls_back_on_timeout(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Fall back when azd times out."""
        assert get_azd_env_value("KEY", "default") == "default"

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch(
        "app.azd_env.subprocess.run",
        side_effect=FileNotFoundError,
    )
    def test_falls_back_on_file_not_found(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Fall back when azd binary disappears between check and run."""
        assert get_azd_env_value("KEY", "default") == "default"


class TestIsAzdAvailable:
    """Tests for is_azd_available function."""

    @patch("app.azd_env.shutil.which", return_value=None)
    def test_returns_false_when_not_installed(
        self, mock_which: MagicMock
    ) -> None:
        """Return False when azd is not on PATH."""
        assert is_azd_available() is False

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch("app.azd_env.subprocess.run")
    def test_returns_true_on_success(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Return True when azd --version succeeds."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        assert is_azd_available() is True

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch(
        "app.azd_env.subprocess.run", side_effect=FileNotFoundError
    )
    def test_returns_false_on_error(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Return False when azd execution raises an exception."""
        assert is_azd_available() is False

    @patch("app.azd_env.shutil.which", return_value="/usr/bin/azd")
    @patch("app.azd_env.subprocess.run")
    def test_returns_false_on_nonzero_exit(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        """Return False when azd --version returns non-zero."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1
        )
        assert is_azd_available() is False
