from __future__ import annotations

import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from typing import Any

import pytest

from external_sli_publisher.publisher import (
    ProbeResult,
    Settings,
    Window,
    combine_sli_samples,
    heartbeat_sample,
    metric_samples,
    missed_window_samples,
    parse_state_datetime,
    probe_endpoint,
    probe_result_to_sli_samples,
    target_window,
    windows_to_publish,
)


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"ok"

    def getcode(self) -> int:
        return self.status


class FakeClock:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0)


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "probe_url": "https://chaos.example.test/",
        "probe_name": "chaos-app-health",
        "remote_write_url": "https://example.test/write",
        "state_blob_url": "https://storage.blob.core.windows.net/state/blob.json",
        "service_name": "chaos-app",
        "environment": "eval",
        "window_seconds": 300,
        "probe_timeout_seconds": 10,
        "max_catchup_windows": 12,
        "not_before": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_rejects_timeout_smaller_than_largest_bucket() -> None:
    with pytest.raises(RuntimeError, match="latency bucket"):
        settings(probe_timeout_seconds=5)


def test_target_window_uses_latest_closed_window() -> None:
    now = datetime(2026, 5, 19, 16, 52, 9, tzinfo=UTC)
    window = target_window(now, settings())
    assert window.start == datetime(2026, 5, 19, 16, 45, tzinfo=UTC)
    assert window.end == datetime(2026, 5, 19, 16, 50, tzinfo=UTC)


def test_first_window_waits_until_not_before_is_reached() -> None:
    cfg = settings(not_before=datetime(2026, 5, 19, 16, 52, tzinfo=UTC))
    early = Window(
        start=datetime(2026, 5, 19, 16, 50, tzinfo=UTC),
        end=datetime(2026, 5, 19, 16, 55, tzinfo=UTC),
    )
    ready = Window(
        start=datetime(2026, 5, 19, 16, 55, tzinfo=UTC),
        end=datetime(2026, 5, 19, 17, 0, tzinfo=UTC),
    )

    assert windows_to_publish(last_published_end=None, target=early, settings=cfg) == []
    assert windows_to_publish(last_published_end=None, target=ready, settings=cfg) == [
        ready
    ]


def test_first_window_without_not_before_publishes_target_only() -> None:
    target = Window(
        start=datetime(2026, 5, 19, 17, 10, tzinfo=UTC),
        end=datetime(2026, 5, 19, 17, 15, tzinfo=UTC),
    )

    assert windows_to_publish(
        last_published_end=None,
        target=target,
        settings=settings(not_before=None),
    ) == [target]


def test_missed_windows_publish_chronological_capped_windows() -> None:
    cfg = settings(max_catchup_windows=2)
    target = Window(
        start=datetime(2026, 5, 19, 17, 10, tzinfo=UTC),
        end=datetime(2026, 5, 19, 17, 15, tzinfo=UTC),
    )
    windows = windows_to_publish(
        last_published_end=datetime(2026, 5, 19, 17, 0, tzinfo=UTC),
        target=target,
        settings=cfg,
    )

    assert windows == [
        Window(
            start=datetime(2026, 5, 19, 17, 0, tzinfo=UTC),
            end=datetime(2026, 5, 19, 17, 5, tzinfo=UTC),
        ),
        Window(
            start=datetime(2026, 5, 19, 17, 5, tzinfo=UTC),
            end=datetime(2026, 5, 19, 17, 10, tzinfo=UTC),
        ),
    ]


def test_no_windows_when_target_is_already_published() -> None:
    target = Window(
        start=datetime(2026, 5, 19, 17, 10, tzinfo=UTC),
        end=datetime(2026, 5, 19, 17, 15, tzinfo=UTC),
    )

    assert (
        windows_to_publish(
            last_published_end=target.end,
            target=target,
            settings=settings(),
        )
        == []
    )


def test_probe_success_is_good_for_availability_and_all_buckets_above_duration() -> (
    None
):
    result = probe_endpoint(
        settings(),
        urlopen=lambda *_args, **_kwargs: FakeResponse(200),
        clock=FakeClock(100.0, 100.25),
    )

    assert result == ProbeResult(
        success=True,
        status_code=200,
        duration_ms=250,
        error=None,
    )
    samples = probe_result_to_sli_samples(result, settings())
    assert samples.availability_good == 1
    assert samples.availability_total == 1
    assert samples.latency_total == 1
    # 250ms duration → buckets >= 0.25s contain the observation, smaller don't
    assert samples.latency_buckets == {
        "0.1": 0,
        "0.25": 1,
        "0.5": 1,
        "1": 1,
        "2": 1,
        "5": 1,
    }


def test_probe_slow_success_is_availability_good_but_outside_smaller_buckets() -> None:
    result = probe_endpoint(
        settings(),
        urlopen=lambda *_args, **_kwargs: FakeResponse(200),
        clock=FakeClock(100.0, 101.5),
    )

    assert result.success is True
    assert result.duration_ms == 1500
    samples = probe_result_to_sli_samples(result, settings())
    assert samples.availability_good == 1
    assert samples.latency_buckets == {
        "0.1": 0,
        "0.25": 0,
        "0.5": 0,
        "1": 0,
        "2": 1,
        "5": 1,
    }


def test_probe_http_error_marks_all_latency_buckets_as_bad() -> None:
    request = urllib.request.Request("https://chaos.example.test/")
    error = urllib.error.HTTPError(
        request.full_url,
        503,
        "Service Unavailable",
        hdrs=Message(),
        fp=None,
    )

    result = probe_endpoint(
        settings(),
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
        clock=FakeClock(100.0, 100.1),
    )

    assert result.success is False
    assert result.status_code == 503
    samples = probe_result_to_sli_samples(result, settings())
    assert samples.availability_good == 0
    assert samples.latency_total == 1
    assert all(value == 0 for value in samples.latency_buckets.values())


def test_probe_url_error_marks_all_latency_buckets_as_bad() -> None:
    result = probe_endpoint(
        settings(),
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("dns")
        ),
        clock=FakeClock(100.0, 100.1),
    )

    assert result.success is False
    assert result.status_code is None
    samples = probe_result_to_sli_samples(result, settings())
    assert samples.availability_good == 0
    assert samples.latency_total == 1
    assert all(value == 0 for value in samples.latency_buckets.values())


def test_missed_windows_are_bad_and_combined_with_current_probe() -> None:
    combined = combine_sli_samples(
        [
            missed_window_samples(2),
            probe_result_to_sli_samples(
                ProbeResult(success=True, status_code=200, duration_ms=250),
                settings(),
            ),
        ]
    )

    assert combined.availability_good == 1
    assert combined.availability_total == 3
    assert combined.latency_total == 3
    # only the current 250ms probe contributes good buckets
    assert combined.latency_buckets == {
        "0.1": 0,
        "0.25": 1,
        "0.5": 1,
        "1": 1,
        "2": 1,
        "5": 1,
    }


def test_metric_samples_emit_single_good_metric_with_le_label_and_total() -> None:
    cfg = settings()
    sample_time = datetime(2026, 5, 19, 16, 52, 9, tzinfo=UTC)
    sli_samples = combine_sli_samples(
        [
            missed_window_samples(2),
            probe_result_to_sli_samples(
                ProbeResult(success=True, status_code=200, duration_ms=250),
                cfg,
            ),
        ]
    )
    samples = metric_samples(sli_samples, cfg, sample_time)

    names = {name for name, *_ in samples}
    assert names == {
        "chaos_app_external_availability_good",
        "chaos_app_external_availability_total",
        "chaos_app_external_latency_total",
        "chaos_app_external_latency_good",
    }
    good_values = {
        labels["le"]: value
        for name, labels, value, _ in samples
        if name == "chaos_app_external_latency_good"
    }
    # 250ms probe success → buckets >= 0.25 are good (=1), smaller are 0;
    # missed windows contribute 0 to all buckets.
    assert good_values == {
        "0.1": 0.0,
        "0.25": 1.0,
        "0.5": 1.0,
        "1": 1.0,
        "2": 1.0,
        "5": 1.0,
    }
    total_value = next(
        value
        for name, _labels, value, _ in samples
        if name == "chaos_app_external_latency_total"
    )
    assert total_value == 3.0
    # `le` is only set on the per-bucket good metric; other series carry the
    # base SLI labels only.
    for name, labels, *_ in samples:
        if name == "chaos_app_external_latency_good":
            assert "le" in labels
        else:
            assert "le" not in labels
    assert all(labels["environment"] == "eval" for _, labels, _, _ in samples)
    assert all(labels["service"] == "chaos-app" for _, labels, _, _ in samples)
    assert all(labels["source"] == "external_probe" for _, labels, _, _ in samples)
    assert all(labels["test"] == "chaos-app-health" for _, labels, _, _ in samples)
    assert all(timestamp == 1779209529000 for *_, timestamp in samples)


def test_heartbeat_sample_uses_current_publish_timestamp() -> None:
    sample_time = datetime(2026, 5, 19, 16, 52, 9, tzinfo=UTC)

    name, labels, value, timestamp = heartbeat_sample(settings(), sample_time)

    assert name == "chaos_app_external_sli_publisher_heartbeat"
    assert labels["environment"] == "eval"
    assert labels["service"] == "chaos-app"
    assert labels["source"] == "external_probe"
    assert labels["test"] == "chaos-app-health"
    assert value == 1.0
    assert timestamp == 1779209529000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-05-19T16:52:00Z", datetime(2026, 5, 19, 16, 52, tzinfo=UTC)),
        ("2026-05-19T16:52:00+00:00", datetime(2026, 5, 19, 16, 52, tzinfo=UTC)),
    ],
)
def test_parse_state_datetime(value: str, expected: datetime) -> None:
    assert parse_state_datetime(value) == expected
