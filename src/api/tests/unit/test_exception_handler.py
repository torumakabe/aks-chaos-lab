from fastapi.testclient import TestClient

from app.main import app


def test_exception_handler_returns_standard_error() -> None:
    async def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/boom", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/boom")
        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "Internal Server Error"
        assert "timestamp" in body
        # detail may be omitted unless LOG_LEVEL=DEBUG; do not assert presence
