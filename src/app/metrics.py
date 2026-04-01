"""Prometheus metrics for SLO monitoring.

アプリ層で HTTP リクエストメトリクスを収集し、/metrics エンドポイントで公開する。
Gateway API (App Routing Istio) の meshless モードでは、Envoy からの
HTTP メトリクス取得が AKS コントローラーの制約により安定しないため、
アプリ層で SLO 監視用メトリクスを直接公開する。
"""

from time import monotonic

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

REQUEST_COUNT = Counter(
    "app_http_requests_total",
    "Total HTTP requests",
    ["method", "status"],
)

REQUEST_DURATION = Histogram(
    "app_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# /metrics と /health はインフラリクエストのため SLO メトリクスから除外
_EXCLUDED_PATHS = frozenset({"/metrics", "/health"})


def setup_metrics(app: FastAPI) -> None:
    """Register Prometheus metrics middleware and /metrics endpoint."""

    @app.middleware("http")
    async def prometheus_metrics_middleware(
        request: Request, call_next: object
    ) -> Response:
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)  # type: ignore[operator]

        start = monotonic()
        response: Response = await call_next(request)  # type: ignore[operator]
        duration = monotonic() - start

        REQUEST_COUNT.labels(
            method=request.method,
            status=str(response.status_code),
        ).inc()
        REQUEST_DURATION.labels(method=request.method).observe(duration)

        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )
