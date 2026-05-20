import logging
from unittest.mock import MagicMock, patch

import pytest

from app.telemetry import (
    ErrorAwareSampler,
    _active_requests_callback,
    _Once,
    _redis_status_callback,
    decrement_active_requests,
    increment_active_requests,
    record_redis_metrics,
    record_redis_status_only,
    record_span_error,
    reset_telemetry,
    setup_telemetry,
    shutdown_telemetry,
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
        patch("app.telemetry.OTLPLogExporter"),
        patch("app.telemetry.BatchLogRecordProcessor"),
        patch("app.telemetry.LoggerProvider"),
        patch("app.telemetry.LoggingHandler"),
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
    reset_telemetry()
    with patch("app.telemetry._meter", None):
        record_redis_metrics(connected=True, latency_ms=5)


def test_record_redis_metrics_disabled() -> None:
    """record_redis_metrics returns early when custom_metrics_enabled is False."""
    reset_telemetry()
    with patch("app.telemetry._meter", MagicMock()):
        with patch("app.config.Settings") as mock_settings:
            mock_settings.return_value.custom_metrics_enabled = False
            record_redis_metrics(connected=True, latency_ms=5)


def test_record_redis_metrics_success() -> None:
    """record_redis_metrics records latency on cached histogram and updates state."""
    reset_telemetry()
    mock_hist = MagicMock()
    with (
        patch("app.telemetry._meter", MagicMock()),
        patch("app.telemetry._redis_latency_hist", mock_hist),
        patch("app.config.Settings") as mock_settings,
    ):
        mock_settings.return_value.custom_metrics_enabled = True
        record_redis_metrics(connected=True, latency_ms=5)
    mock_hist.record.assert_called_once_with(5)
    # backing state は ObservableGauge callback 用
    import app.telemetry as tm

    assert tm._redis_connected_state == 1
    reset_telemetry()


def test_record_redis_metrics_disconnected() -> None:
    """record_redis_metrics records 0 to backing state and skips histogram."""
    reset_telemetry()
    mock_hist = MagicMock()
    with (
        patch("app.telemetry._meter", MagicMock()),
        patch("app.telemetry._redis_latency_hist", mock_hist),
        patch("app.config.Settings") as mock_settings,
    ):
        mock_settings.return_value.custom_metrics_enabled = True
        record_redis_metrics(connected=False, latency_ms=-1)
    mock_hist.record.assert_not_called()
    import app.telemetry as tm

    assert tm._redis_connected_state == 0
    reset_telemetry()


def test_record_redis_status_only_does_not_write_histogram() -> None:
    """record_redis_status_only updates backing state without touching histogram."""
    reset_telemetry()
    mock_hist = MagicMock()
    with (
        patch("app.telemetry._meter", MagicMock()),
        patch("app.telemetry._redis_latency_hist", mock_hist),
        patch("app.config.Settings") as mock_settings,
    ):
        mock_settings.return_value.custom_metrics_enabled = True
        record_redis_status_only(connected=True)
        record_redis_status_only(connected=False)
    mock_hist.record.assert_not_called()
    import app.telemetry as tm

    assert tm._redis_connected_state == 0
    reset_telemetry()


def test_record_redis_status_only_disabled() -> None:
    """record_redis_status_only returns early when custom_metrics_enabled is False."""
    reset_telemetry()
    with (
        patch("app.telemetry._meter", MagicMock()),
        patch("app.config.Settings") as mock_settings,
    ):
        mock_settings.return_value.custom_metrics_enabled = False
        record_redis_status_only(connected=True)
    import app.telemetry as tm

    # state is unchanged (default -1)
    assert tm._redis_connected_state == -1
    reset_telemetry()


def test_redis_status_callback_returns_empty_when_unknown() -> None:
    """ObservableGauge callback yields nothing while state is -1 (unknown)."""
    reset_telemetry()
    obs = _redis_status_callback(MagicMock())
    assert obs == []


def test_redis_status_callback_returns_state_when_known() -> None:
    """ObservableGauge callback yields current state once recorded."""
    reset_telemetry()
    with (
        patch("app.telemetry._meter", MagicMock()),
        patch("app.config.Settings") as mock_settings,
    ):
        mock_settings.return_value.custom_metrics_enabled = True
        record_redis_status_only(connected=True)
    obs = _redis_status_callback(MagicMock())
    assert len(obs) == 1
    assert obs[0].value == 1
    reset_telemetry()


def test_error_aware_sampler_always_samples_chaos_path() -> None:
    """ErrorAwareSampler returns RECORD_AND_SAMPLE for chaos/error spans."""
    from opentelemetry.sdk.trace.sampling import Decision

    sampler = ErrorAwareSampler(rate=0.0)
    # rate=0.0 でも chaos path は sampled
    result = sampler.should_sample(
        parent_context=None,
        trace_id=0x1234,
        name="GET /chaos/redis-failure",
        attributes={"http.target": "/chaos/redis-failure"},
    )
    assert result.decision == Decision.RECORD_AND_SAMPLE


def test_error_aware_sampler_always_samples_error_attribute() -> None:
    """ErrorAwareSampler matches error pattern via attribute keys."""
    from opentelemetry.sdk.trace.sampling import Decision

    sampler = ErrorAwareSampler(rate=0.0)
    result = sampler.should_sample(
        parent_context=None,
        trace_id=0x5678,
        name="POST /api/throw",
        attributes={"url.path": "/api/throw"},
    )
    assert result.decision == Decision.RECORD_AND_SAMPLE


def test_error_aware_sampler_delegates_to_ratio_for_normal_path() -> None:
    """ErrorAwareSampler delegates non-error spans to TraceIdRatioBased."""
    from opentelemetry.sdk.trace.sampling import Decision

    sampler = ErrorAwareSampler(rate=0.0)
    # rate=0.0 → 通常 path は DROP
    result = sampler.should_sample(
        parent_context=None,
        trace_id=0xABCD,
        name="GET /api/users",
        attributes={"http.target": "/api/users"},
    )
    assert result.decision == Decision.DROP


def test_error_aware_sampler_description() -> None:
    """ErrorAwareSampler reports its sampling rate in description."""
    sampler = ErrorAwareSampler(rate=0.25)
    assert "0.25" in sampler.get_description()
    assert "ErrorAwareSampler" in sampler.get_description()


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


# --- chaos_app.active_requests gauge --------------------------------------


def test_increment_decrement_active_requests_pair() -> None:
    """increment/decrement のペアで count が +1/-1 し、初期 0 に戻る。"""
    reset_telemetry()
    import app.telemetry as tm

    assert tm._active_requests_count == 0
    increment_active_requests()
    assert tm._active_requests_count == 1
    increment_active_requests()
    assert tm._active_requests_count == 2
    decrement_active_requests()
    decrement_active_requests()
    assert tm._active_requests_count == 0
    reset_telemetry()


def test_decrement_active_requests_underflow_protection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """count が 0 のとき decrement しても 0 のままで負値にならない。"""
    reset_telemetry()
    import app.telemetry as tm

    assert tm._active_requests_count == 0
    with caplog.at_level(logging.WARNING):
        decrement_active_requests()
    assert tm._active_requests_count == 0
    assert "underflow" in caplog.text.lower() or "non-positive" in caplog.text.lower()
    reset_telemetry()


def test_active_requests_callback_returns_current_count() -> None:
    """callback は現在の count を Observation で返す。"""
    reset_telemetry()
    obs = _active_requests_callback(MagicMock())
    assert len(obs) == 1
    assert obs[0].value == 0

    increment_active_requests()
    increment_active_requests()
    obs = _active_requests_callback(MagicMock())
    assert len(obs) == 1
    assert obs[0].value == 2

    decrement_active_requests()
    obs = _active_requests_callback(MagicMock())
    assert obs[0].value == 1
    reset_telemetry()


def test_reset_telemetry_resets_active_requests_count() -> None:
    """reset_telemetry は active_requests_count を 0 に戻す。"""
    increment_active_requests()
    increment_active_requests()
    import app.telemetry as tm

    assert tm._active_requests_count == 2
    reset_telemetry()
    assert tm._active_requests_count == 0


def test_setup_telemetry_creates_active_requests_gauge() -> None:
    """setup_telemetry が ObservableGauge `chaos_app.active_requests` を作成。"""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://localhost:4318/v1/metrics",
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
        import app.telemetry as tm

        assert tm._active_requests_gauge is not None
    reset_telemetry()


# --- OTLP logs pipeline ----------------------------------------------------


def _patches_for_logs_pipeline_enabled():
    """Common patches for tests that exercise the logs pipeline.

    実 LoggingHandler はテスト用途で安全に instantiate できる (logger_provider
    のみ参照、emit は呼ばれない) ため敢えて patch しない。これにより
    LoggingHandler.level / `app` logger への attach 状態などを実物属性で検証できる。
    """
    return (
        patch("app.telemetry.BatchSpanProcessor"),
        patch("app.telemetry.OTLPSpanExporter"),
        patch("app.telemetry.OTLPMetricExporter"),
        patch("app.telemetry.PeriodicExportingMetricReader"),
        patch("app.telemetry.OTLPLogExporter"),
        patch("app.telemetry.BatchLogRecordProcessor"),
        patch("app.telemetry.LoggerProvider"),
        patch("app.telemetry.set_logger_provider"),
    )


def test_setup_telemetry_creates_logger_provider_with_logs_endpoint() -> None:
    """OTEL_EXPORTER_OTLP_LOGS_ENDPOINT が設定されると logs pipeline が作成される。

    `app` logger にだけ LoggingHandler が attach され、root には attach されない。
    handler level が settings.log_level (デフォルト INFO) で設定される。
    """
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/logs",
        "LOG_LEVEL": "INFO",
    }
    bsp, sp_exp, m_exp, m_reader, log_exp, blp, lp, slp = (
        _patches_for_logs_pipeline_enabled()
    )
    with (
        patch.dict("os.environ", env, clear=False),
        bsp,
        sp_exp,
        m_exp,
        m_reader,
        log_exp as mock_log_exp,
        blp as mock_blp,
        lp as mock_lp,
        slp as mock_slp,
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())

        import app.telemetry as tm

        # LoggerProvider was constructed and registered as global.
        assert mock_lp.called
        assert mock_blp.called
        assert mock_log_exp.called
        assert mock_slp.called
        assert tm._logger_provider is not None
        # LoggingHandler was attached to "app" logger but not root.
        app_logger = logging.getLogger("app")
        root_logger = logging.getLogger()
        assert tm._log_handler is not None
        assert any(h is tm._log_handler for h in app_logger.handlers)
        assert not any(h is tm._log_handler for h in root_logger.handlers)
        # Handler level reflects LOG_LEVEL=INFO.
        assert tm._log_handler.level == logging.INFO
    reset_telemetry()


def test_setup_telemetry_creates_logger_provider_with_unified_endpoint() -> None:
    """unified OTEL_EXPORTER_OTLP_ENDPOINT でも logs pipeline が作成される。"""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
    }
    bsp, sp_exp, m_exp, m_reader, log_exp, blp, lp, slp = (
        _patches_for_logs_pipeline_enabled()
    )
    with (
        patch.dict("os.environ", env, clear=False),
        bsp,
        sp_exp,
        m_exp,
        m_reader,
        log_exp,
        blp,
        lp,
        slp,
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())

        import app.telemetry as tm

        assert tm._logger_provider is not None
        assert tm._log_handler is not None
    reset_telemetry()


def test_setup_telemetry_skips_logs_when_only_traces_endpoint() -> None:
    """OTEL_EXPORTER_OTLP_TRACES_ENDPOINT のみだと logs pipeline は作成されない。

    OTLPLogExporter が localhost:4318/v1/logs に fallback して export error を
    出すのを防ぐため、専用 guard で skip することを保証する。
    """
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
        patch("app.telemetry.OTLPLogExporter") as mock_log_exp,
        patch("app.telemetry.BatchLogRecordProcessor") as mock_blp,
        patch("app.telemetry.LoggerProvider") as mock_lp,
        patch("app.telemetry.set_logger_provider") as mock_slp,
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())

        import app.telemetry as tm

        assert not mock_lp.called
        assert not mock_blp.called
        assert not mock_log_exp.called
        assert not mock_slp.called
        assert tm._logger_provider is None
        assert tm._log_handler is None
    reset_telemetry()


def test_setup_telemetry_log_handler_level_respects_log_level() -> None:
    """LOG_LEVEL=WARNING で handler level が WARNING になる (DEBUG record を抑制)。"""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/logs",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
        "LOG_LEVEL": "WARNING",
    }
    bsp, sp_exp, m_exp, m_reader, log_exp, blp, lp, slp = (
        _patches_for_logs_pipeline_enabled()
    )
    with (
        patch.dict("os.environ", env, clear=False),
        bsp,
        sp_exp,
        m_exp,
        m_reader,
        log_exp,
        blp,
        lp,
        slp,
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())

        import app.telemetry as tm

        assert tm._log_handler is not None
        assert tm._log_handler.level == logging.WARNING
    reset_telemetry()


def test_setup_telemetry_disables_logging_instrumentor_auto_handler() -> None:
    """LoggingInstrumentor は enable_log_auto_instrumentation=False で呼ばれる。

    auto handler が root logger に LoggingHandler を勝手に attach すると、
    手動 handler と二重送信になるため必ず無効化する。
    """
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/logs",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
    }
    bsp, sp_exp, m_exp, m_reader, log_exp, blp, lp, slp = (
        _patches_for_logs_pipeline_enabled()
    )
    with (
        patch.dict("os.environ", env, clear=False),
        bsp,
        sp_exp,
        m_exp,
        m_reader,
        log_exp,
        blp,
        lp,
        slp,
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        instrument_mock = MagicMock()
        mock_li.return_value.instrument = instrument_mock
        setup_telemetry(DummyApp())

        instrument_mock.assert_called_once()
        kwargs = instrument_mock.call_args.kwargs
        assert kwargs.get("enable_log_auto_instrumentation") is False
        assert kwargs.get("set_logging_format") is True
    reset_telemetry()


def test_reset_telemetry_removes_log_handler() -> None:
    """reset_telemetry が `app` logger から LoggingHandler を取り外す。"""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/logs",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
    }
    bsp, sp_exp, m_exp, m_reader, log_exp, blp, lp, slp = (
        _patches_for_logs_pipeline_enabled()
    )
    with (
        patch.dict("os.environ", env, clear=False),
        bsp,
        sp_exp,
        m_exp,
        m_reader,
        log_exp,
        blp,
        lp,
        slp,
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())

        import app.telemetry as tm

        attached_handler = tm._log_handler
        assert attached_handler is not None
        assert any(h is attached_handler for h in logging.getLogger("app").handlers)

    reset_telemetry()
    import app.telemetry as tm

    assert tm._log_handler is None
    assert tm._logger_provider is None
    assert not any(h is attached_handler for h in logging.getLogger("app").handlers)


def test_setup_telemetry_no_duplicate_log_handlers() -> None:
    """setup_telemetry の重複呼び出しでも `app` logger の handler は 1 個のまま。"""
    reset_telemetry()
    env = {
        "TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://localhost:4318/v1/logs",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces",
    }
    bsp, sp_exp, m_exp, m_reader, log_exp, blp, lp, slp = (
        _patches_for_logs_pipeline_enabled()
    )
    with (
        patch.dict("os.environ", env, clear=False),
        bsp,
        sp_exp,
        m_exp,
        m_reader,
        log_exp,
        blp,
        lp,
        slp,
        patch("app.telemetry.FastAPIInstrumentor") as mock_fai,
        patch("app.telemetry.RedisInstrumentor") as mock_ri,
        patch("app.telemetry.LoggingInstrumentor") as mock_li,
    ):
        mock_fai.instrument_app = MagicMock()
        mock_ri.return_value.instrument = MagicMock()
        mock_li.return_value.instrument = MagicMock()
        setup_telemetry(DummyApp())
        setup_telemetry(DummyApp())  # second call is a no-op (Once guard)

        import app.telemetry as tm

        app_logger = logging.getLogger("app")
        attached = [h for h in app_logger.handlers if h is tm._log_handler]
        assert len(attached) == 1
    reset_telemetry()


def test_shutdown_telemetry_flushes_and_shuts_down_provider() -> None:
    """shutdown_telemetry は force_flush と shutdown を呼ぶ (best-effort)。"""
    reset_telemetry()
    import app.telemetry as tm

    mock_provider = MagicMock()
    tm._logger_provider = mock_provider
    shutdown_telemetry()
    mock_provider.force_flush.assert_called_once()
    mock_provider.shutdown.assert_called_once()
    reset_telemetry()


def test_shutdown_telemetry_noop_when_not_initialized() -> None:
    """shutdown_telemetry は LoggerProvider 未初期化なら何もしない。"""
    reset_telemetry()
    # Should not raise and should not require any provider
    shutdown_telemetry()
