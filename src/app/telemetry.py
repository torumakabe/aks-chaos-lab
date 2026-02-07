import logging
import os
from contextlib import suppress
from threading import Lock
from typing import Any

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)


# Thread-safe Once pattern implementation (follows OpenTelemetry standards)
class _Once:
    """Ensures a function is called exactly once across multiple threads."""

    def __init__(self):
        self._called = False
        self._lock = Lock()

    def do_once(self, func, *args, **kwargs):
        """Execute func once and return True if executed, False if already called."""
        if self._called:
            return False

        with self._lock:
            if self._called:
                return False
            func(*args, **kwargs)
            self._called = True
            return True


# Global telemetry components
_meter: metrics.Meter | None = None
_tracer: trace.Tracer | None = None

# Standard OpenTelemetry Once pattern for preventing duplicate initialization
_setup_once = _Once()
_instrumentation_once = _Once()


def setup_telemetry(app: Any, connection_string: str | None = None) -> None:
    """Configure Azure Monitor OpenTelemetry and instrumentation.

    - Respects TELEMETRY_* settings and APPLICATIONINSIGHTS_CONNECTION_STRING
    - Configures sampling via OTEL_* env vars when sampling_rate < 1.0
    - Instruments FastAPI (excludes health), Redis, and logging
    - Uses Once pattern to prevent duplicate initialization (thread-safe)
    """

    def _setup_core():
        """Core setup function executed only once."""
        # Lazy import to avoid circular dependency
        from app.config import Settings

        settings = Settings()
        if not settings.telemetry_enabled:
            logger.info("Telemetry disabled via TELEMETRY_ENABLED")
            return

        conn = (
            connection_string
            or settings.applicationinsights_connection_string
            or settings.appinsights_connection_string
            or os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
        )
        if not conn:
            logger.info("No APPLICATIONINSIGHTS_CONNECTION_STRING; telemetry disabled")
            return

        try:
            # Standard resource attributes
            resource = Resource.create(
                {"service.name": "app", "service.version": "0.1.0"}
            )

            # Configure trace sampling via standard OTEL env vars
            sampling_rate = float(settings.telemetry_sampling_rate or 1.0)
            if sampling_rate < 1.0:
                os.environ["OTEL_TRACES_SAMPLER"] = "traceidratio"
                os.environ["OTEL_TRACES_SAMPLER_ARG"] = str(sampling_rate)

            # Configure Azure Monitor exporter
            # Disable psycopg2 instrumentation as we don't use PostgreSQL
            configure_azure_monitor(
                connection_string=conn,
                resource=resource,
                instrumentation_options={"psycopg2": {"enabled": False}},
            )

            # Initialize globals
            global _meter, _tracer
            _meter = metrics.get_meter("aks-chaos-lab", "0.1.0")
            _tracer = trace.get_tracer("aks-chaos-lab", "0.1.0")

            logger.info("Telemetry configured (Azure Monitor + OTel)")
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to configure telemetry: %s", e)

    def _setup_instrumentation():
        """Instrumentation setup executed only once."""
        try:
            with suppress(Exception):
                FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
            with suppress(Exception):
                RedisInstrumentor().instrument()
            with suppress(Exception):
                LoggingInstrumentor().instrument(set_logging_format=True)
            logger.debug("Instrumentation completed")
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to configure instrumentation: %s", e)

    # Execute core setup once
    was_executed = _setup_once.do_once(_setup_core)
    if was_executed:
        logger.debug("Telemetry core setup executed")
    else:
        logger.debug("Telemetry already configured, skipping duplicate initialization")

    # Execute instrumentation once
    was_executed = _instrumentation_once.do_once(_setup_instrumentation)
    if was_executed:
        logger.debug("Instrumentation setup executed")
    else:
        logger.debug("Instrumentation already configured, skipping duplicate setup")


def record_span_error(exc: Exception) -> None:
    """Record exception on current span."""
    try:
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.set_status(Status(StatusCode.ERROR, str(exc)))
            current_span.record_exception(exc)
    except Exception as e:  # noqa: BLE001
        logger.debug("record_span_error failed: %s", e)


def reset_telemetry() -> None:
    """Reset telemetry configuration state (for testing).

    Note: This follows OpenTelemetry testing patterns and should only
    be used in test environments to reset Once guards.
    """
    global _setup_once, _instrumentation_once
    _setup_once = _Once()
    _instrumentation_once = _Once()
    logger.debug("Telemetry state reset for testing")


def record_redis_metrics(connected: bool, latency_ms: int) -> None:
    """Record Redis connection metrics if custom metrics enabled."""
    # Lazy import to avoid circular dependency
    from app.config import Settings

    settings = Settings()
    if not settings.custom_metrics_enabled or not _meter:
        return
    try:
        conn_gauge = _meter.create_gauge(
            name="redis_connection_status",
            description="Redis connection status (1=connected, 0=disconnected)",
        )
        conn_gauge.set(1 if connected else 0)

        if connected and latency_ms >= 0:
            lat_hist = _meter.create_histogram(
                name="redis_connection_latency_ms",
                description="Redis connection latency (ms)",
                unit="ms",
            )
            lat_hist.record(latency_ms)
    except Exception as e:  # noqa: BLE001
        logger.debug("record_redis_metrics failed: %s", e)
