from app.telemetry import setup_telemetry


class DummyApp:
    state = type("S", (), {})()


def test_setup_telemetry_noop_without_endpoint() -> None:
    # Should not raise when OTEL_EXPORTER_OTLP_ENDPOINT is not set
    setup_telemetry(DummyApp())
