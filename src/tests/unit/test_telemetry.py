from app.telemetry import setup_telemetry


class DummyApp:
    state = type("S", (), {})()


def test_setup_telemetry_noop_without_connection_string() -> None:
    # Should not raise when connection string is missing
    setup_telemetry(DummyApp(), None)
