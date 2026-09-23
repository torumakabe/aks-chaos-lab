from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tasks  # noqa: E402

NOW = datetime(2026, 9, 22, 14, 30, tzinfo=UTC)
CUTOFF = "2026-09-15T00:00:00Z"


def write_lock(path: Path, cutoff: str | None) -> Path:
    lock = path / "uv.lock"
    options = f'\n[options]\nexclude-newer = "{cutoff}"\n' if cutoff else "\n"
    lock.write_text(f"version = 1{options}", encoding="utf-8")
    return lock


def test_cutoff_is_floored_to_a_utc_day() -> None:
    assert tasks.lock_cutoff_timestamp(NOW) == CUTOFF


def test_cutoff_is_stable_across_a_day() -> None:
    later = NOW.replace(hour=23, minute=59)
    assert tasks.lock_cutoff_timestamp(later) == tasks.lock_cutoff_timestamp(NOW)


def test_cutoff_is_never_newer_than_the_required_age() -> None:
    cutoff = datetime.fromisoformat(tasks.lock_cutoff_timestamp(NOW))
    assert cutoff <= NOW - timedelta(days=tasks.PYTHON_RELEASE_COOLDOWN_DAYS)


def test_cutoff_from_a_slightly_ahead_clock_is_accepted(tmp_path: Path) -> None:
    lock = write_lock(tmp_path, "2026-09-16T00:00:00Z")
    assert tasks.recorded_lock_cutoff(lock, NOW) == "2026-09-16T00:00:00Z"


def test_recorded_cutoff_is_returned(tmp_path: Path) -> None:
    lock = write_lock(tmp_path, CUTOFF)
    assert tasks.recorded_lock_cutoff(lock, NOW) == CUTOFF


def test_missing_cutoff_is_rejected(tmp_path: Path) -> None:
    lock = write_lock(tmp_path, None)
    with pytest.raises(tasks.LockCutoffError):
        tasks.recorded_lock_cutoff(lock, NOW)


def test_too_recent_cutoff_is_rejected(tmp_path: Path) -> None:
    lock = write_lock(tmp_path, "2026-09-17T00:00:00Z")
    with pytest.raises(tasks.LockCutoffError):
        tasks.recorded_lock_cutoff(lock, NOW)


def test_unreadable_cutoff_is_rejected(tmp_path: Path) -> None:
    lock = write_lock(tmp_path, "last tuesday")
    with pytest.raises(tasks.LockCutoffError):
        tasks.recorded_lock_cutoff(lock, NOW)


def test_check_passes_the_recorded_cutoff_to_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_lock(tmp_path, CUTOFF)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    calls: list[list[str]] = []

    class Completed:
        returncode = 0

    def fake_run(args: Sequence[str], **kwargs: object) -> Completed:
        calls.append(list(args))
        return Completed()

    monkeypatch.setattr(tasks, "run", fake_run)

    tasks.target_check_uv_lock()

    assert calls == [
        [
            "uv",
            "lock",
            "--check",
            "--offline",
            "--default-index",
            tasks.PUBLIC_PYPI_INDEX,
            "--exclude-newer",
            CUTOFF,
        ]
    ]


def test_check_reports_how_to_recover_from_a_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_lock(tmp_path, CUTOFF)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)

    class Failed:
        returncode = 1

    monkeypatch.setattr(tasks, "run", lambda args, **kwargs: Failed())

    with pytest.raises(SystemExit):
        tasks.target_check_uv_lock()

    assert "adopt-public-lock" in capsys.readouterr().err


def test_check_does_not_run_uv_without_a_recorded_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_lock(tmp_path, None)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)

    def fail_on_uv(*args: object, **kwargs: object) -> None:
        pytest.fail("check-uv-lock must not run uv without a recorded cutoff")

    monkeypatch.setattr(tasks, "run", fail_on_uv)

    with pytest.raises(SystemExit):
        tasks.target_check_uv_lock()
