"""Behavioral tests for the active_requests_middleware.

middleware の振る舞い (handler 実行中に count が増え、終了で戻る; /health 除外)
を TestClient で検証する。Starlette/FastAPI の middleware stack 内部順序には
依存せず、振る舞いのみを assert する。
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, get_redis_client, get_settings
from app.telemetry import _active_requests_callback, reset_telemetry


def _test_settings() -> Settings:
    s = Settings()
    s.redis_enabled = False
    s.redis_host = None
    s.telemetry_enabled = False
    return s


@pytest.fixture(scope="module")
def client_with_probe() -> Generator[TestClient]:
    """TestClient with a /__probe endpoint that returns the current count.

    handler 実行中に callback を呼ぶことで「リクエスト中の count」を
    外側から観測できる ("incremented" の値を response 経由で受け取る)。
    """
    _cached = _test_settings()
    app.dependency_overrides[get_settings] = lambda: _cached
    app.dependency_overrides[get_redis_client] = lambda: None

    @app.get("/__probe_active_requests")
    def _probe() -> dict[str, int]:
        obs = _active_requests_callback(object())
        return {"value": int(obs[0].value) if obs else -1}

    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        # remove the probe route to avoid leaking into other test modules
        app.router.routes = [
            r
            for r in app.router.routes
            if getattr(r, "path", None) != "/__probe_active_requests"
        ]


def test_active_requests_count_is_one_during_request(
    client_with_probe: TestClient,
) -> None:
    """handler 内で callback を呼ぶと count==1 (自分自身) を観測する。"""
    reset_telemetry()
    r = client_with_probe.get("/__probe_active_requests")
    assert r.status_code == 200
    assert r.json()["value"] == 1


def test_active_requests_count_returns_to_zero_after_request(
    client_with_probe: TestClient,
) -> None:
    """request 終了後 (response 受信時点) には count が 0 に戻っている。"""
    reset_telemetry()
    client_with_probe.get("/__probe_active_requests")
    obs = _active_requests_callback(object())
    assert obs[0].value == 0


def test_active_requests_health_is_excluded(
    client_with_probe: TestClient,
) -> None:
    """/health は increment しない (kubelet probe 由来のため除外)。"""
    reset_telemetry()
    r = client_with_probe.get("/health")
    assert r.status_code in (200, 503)
    obs = _active_requests_callback(object())
    assert obs[0].value == 0


def test_active_requests_handles_handler_exception() -> None:
    """handler が例外を出しても finally で decrement され count は 0 に戻る。

    `raise_server_exceptions=False` で general_exception_handler の 500 応答
    が返るようにする (デフォルト True だと TestClient が例外を再送出する)。
    """
    reset_telemetry()

    async def _raises() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/__probe_raise", _raises, methods=["GET"])
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/__probe_raise")
            assert r.status_code == 500
        obs = _active_requests_callback(object())
        assert obs[0].value == 0
    finally:
        app.router.routes = [
            r
            for r in app.router.routes
            if getattr(r, "path", None) != "/__probe_raise"
        ]
