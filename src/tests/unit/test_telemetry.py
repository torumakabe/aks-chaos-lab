import logging
from unittest.mock import MagicMock, patch

import pytest

from app.telemetry import (
    _Once,
    record_redis_metrics,
    record_span_error,
    reset_telemetry,
    setup_telemetry,
)


class DummyApp:
    state = type("S", (), {})()


def test_setup_telemetry_noop_without_endpoint() -> None:
    # Should not raise when OTEL_EXPORTER_OTLP_ENDPOINT is not set
    reset_telemetry()
    setup_telemetry(DummyApp())


def test_setup_telemetry_noop_when_disabled() -> None:
    """Telemetry setup does nothing when TELEMETRY_ENABLED=false."""
    reset_telemetry()
    with patch.dict("os.environ", {"TELEMETRY_ENABLED": "false"}):
        setup_telemetry(DummyApp())


def test_setup_telemetry_with_otlp_endpoint() -> None:
    """Telemetry setup initializes providers when OTLP endpoint is set."""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://localhost:4318/v1/metrics",
        "TELEMETRY_SAMPLING_RATE": "0.5",
    }
    with (
        patch.dict("os.environ", env, clear=False),
        patch("app.telemetry.BatchSpanProcessor"),
        patch("app.telemetry.OTLPSpanExporter"),
        patch("app.telemetry.OTLPMetricExporter"),
        patch("app.telemetry.PeriodicExportingMetricReader"),
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())
    reset_telemetry()


def test_setup_telemetry_with_unified_endpoint() -> None:
    """Telemetry setup works with unified OTEL_EXPORTER_OTLP_ENDPOINT."""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
    }
    with (
        patch.dict("os.environ", env, clear=False),
        patch("app.telemetry.BatchSpanProcessor"),
        patch("app.telemetry.OTLPSpanExporter"),
        patch("app.telemetry.OTLPMetricExporter"),
        patch("app.telemetry.PeriodicExportingMetricReader"),
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())
    reset_telemetry()


def test_setup_telemetry_duplicate_initialization() -> None:
    """Second call to setup_telemetry is a no-op (Once pattern)."""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
    }
    with (
        patch.dict("os.environ", env, clear=False),
        patch("app.telemetry.BatchSpanProcessor"),
        patch("app.telemetry.OTLPSpanExporter"),
        patch("app.telemetry.OTLPMetricExporter"),
        patch("app.telemetry.PeriodicExportingMetricReader"),
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())
        # Second call should be a no-op
        setup_telemetry(DummyApp())
    reset_telemetry()


def test_once_pattern() -> None:
    """_Once ensures function is called exactly once."""
    counter = {"value": 0}

    def increment():
        counter["value"] += 1

    once = _Once()
    assert once.do_once(increment) is True
    assert counter["value"] == 1
    assert once.do_once(increment) is False
    assert counter["value"] == 1


def test_record_span_error_no_active_span() -> None:
    """record_span_error does nothing without an active span."""
    record_span_error(ValueError("test error"))


def test_record_span_error_with_recording_span() -> None:
    """record_span_error records exception on recording span."""
    mock_span = MagicMock()
    mock_span.is_recording.return_value = True
    with patch("app.telemetry.trace.get_current_span", return_value=mock_span):
        record_span_error(ValueError("test error"))
    mock_span.set_status.assert_called_once()
    mock_span.record_exception.assert_called_once()


def test_record_span_error_with_non_recording_span() -> None:
    """record_span_error skips non-recording spans."""
    mock_span = MagicMock()
    mock_span.is_recording.return_value = False
    with patch("app.telemetry.trace.get_current_span", return_value=mock_span):
        record_span_error(ValueError("test"))
    mock_span.set_status.assert_not_called()


def test_record_redis_metrics_no_meter() -> None:
    """record_redis_metrics returns early when meter is None."""
    with patch("app.telemetry._meter", None):
        record_redis_metrics(connected=True, latency_ms=5)


def test_record_redis_metrics_disabled() -> None:
    """record_redis_metrics returns early when custom_metrics_enabled is False."""
    with patch("app.telemetry._meter", MagicMock()):
        with patch("app.config.Settings") as mock_settings:
            mock_settings.return_value.custom_metrics_enabled = False
            record_redis_metrics(connected=True, latency_ms=5)


def test_record_redis_metrics_success() -> None:
    """record_redis_metrics records gauge and histogram."""
    mock_meter = MagicMock()
    mock_gauge = MagicMock()
    mock_hist = MagicMock()
    mock_meter.create_gauge.return_value = mock_gauge
    mock_meter.create_histogram.return_value = mock_hist
    with patch("app.telemetry._meter", mock_meter):
        with patch("app.config.Settings") as mock_settings:
            mock_settings.return_value.custom_metrics_enabled = True
            record_redis_metrics(connected=True, latency_ms=5)
    mock_gauge.set.assert_called_once_with(1)
    mock_hist.record.assert_called_once_with(5)


def test_record_redis_metrics_disconnected() -> None:
    """record_redis_metrics records 0 for disconnected state."""
    mock_meter = MagicMock()
    mock_gauge = MagicMock()
    mock_meter.create_gauge.return_value = mock_gauge
    with patch("app.telemetry._meter", mock_meter):
        with patch("app.config.Settings") as mock_settings:
            mock_settings.return_value.custom_metrics_enabled = True
            record_redis_metrics(connected=False, latency_ms=-1)
    mock_gauge.set.assert_called_once_with(0)


def test_reset_telemetry() -> None:
    """reset_telemetry resets Once guards."""
    reset_telemetry()


def test_setup_telemetry_exception_handling(caplog: pytest.LogCaptureFixture) -> None:
    """setup_telemetry handles exceptions gracefully."""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
    }
    with (
        patch.dict("os.environ", env, clear=False),
        patch("app.telemetry.Resource.create", side_effect=RuntimeError("boom")),
        caplog.at_level(logging.ERROR),
    ):
        setup_telemetry(DummyApp())
    assert "Failed to configure telemetry" in caplog.text
    reset_telemetry()
