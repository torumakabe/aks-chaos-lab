"""Main FastAPI application module (no chaos API)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace

from app.config import Settings
from app.models import ErrorResponse, HealthResponse, MainResponse
from app.redis_client import RedisClient
from app.telemetry import record_span_error, setup_telemetry


# Global instances
def _load_settings() -> Settings:
    """Build Settings from environment variables.

    pydantic-settings reads env vars at construction time.
    """
    return Settings()


settings: Settings = _load_settings()
redis_client: RedisClient | None = None

# Health check cache (5-second TTL to reduce Redis load)
# Cache both payload and HTTP status code to preserve semantics for unhealthy
_health_cache: dict[str, Any] = {}
_HEALTH_CACHE_TTL = 5.0  # seconds

# Lightweight request counter for sampling (avoid non-deterministic hash sampling)
_request_counter: int = 0


def _is_health_cache_valid() -> bool:
    ts: float | None = (
        _health_cache.get("_ts")
        if isinstance(_health_cache.get("_ts"), int | float)
        else None
    )
    if ts is None:
        return False
    elapsed: float = monotonic() - float(ts)
    return elapsed < _HEALTH_CACHE_TTL


def _update_health_cache(resp: HealthResponse, status_code: int) -> None:
    _health_cache["payload"] = resp
    _health_cache["status_code"] = status_code
    _health_cache["_ts"] = monotonic()


# --- Dependency Injection providers ---
# Override these via app.dependency_overrides in tests.


def get_settings(request: Request) -> Settings:
    """Return runtime Settings, preferring app.state if available."""
    state_settings = getattr(
        getattr(request.app, "state", None), "settings", None
    )
    if isinstance(state_settings, Settings):
        return state_settings
    return settings


def get_redis_client(request: Request) -> RedisClient | None:
    """Return RedisClient from app.state, falling back to global."""
    state_client = getattr(
        getattr(request.app, "state", None), "redis_client", None
    )
    if state_client is not None:
        return state_client
    return redis_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan with graceful startup and shutdown.

    This function handles:
    - Initializing Redis connection
    - Proper cleanup of resources during shutdown

    Note: Uvicorn automatically handles SIGINT/SIGTERM signals for graceful shutdown.
    """
    global redis_client
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting AKS Chaos Lab")

    # Setup Redis
    if settings.redis_enabled and settings.redis_host:
        logger.info(
            "Setting up Redis client for %s:%s",
            settings.redis_host,
            settings.redis_port,
        )
        redis_client = RedisClient(settings.redis_host, settings.redis_port, settings)
        try:
            await redis_client.connect()
            logger.info("Successfully connected to Redis at startup")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to connect to Redis at startup: %s", e)

    # Expose runtime dependencies via app.state
    with suppress(Exception):
        app.state.settings = settings
        app.state.redis_client = redis_client

    yield

    logger.info("Shutting down AKS Chaos Lab")

    # Graceful shutdown: wait a bit for in-flight requests to complete
    # Uvicorn will stop accepting new connections when it receives SIGTERM/SIGINT
    logger.info("Waiting for in-flight requests to complete...")
    await asyncio.sleep(5)  # Allow time for in-flight requests to complete

    # Clean up Redis connection
    if redis_client:
        logger.info("Closing Redis connection")
        await redis_client.close()

    with suppress(Exception):
        app.state.redis_client = None

    logger.info("Application shutdown complete")


app = FastAPI(title="AKS Chaos Lab", lifespan=lifespan)
setup_telemetry(app)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Ensure X-Request-ID is present and propagate to response and tracing.

    - If header is absent, generate a UUIDv4.
    - Store into request.state for handlers, echo in response header.
    - Annotate current span attribute for correlation.
    """
    req_id = request.headers.get("X-Request-ID") or str(uuid4())
    # Expose to handlers
    with suppress(Exception):
        request.state.request_id = req_id

    # Attach to current span if present
    with suppress(Exception):
        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attribute("http.request_id", req_id)

    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: D401
    """Handle all uncaught exceptions with standardized error response."""
    logging.getLogger(__name__).exception("Unhandled exception: %s", exc)
    record_span_error(exc)
    error_response = ErrorResponse(
        error="Internal Server Error",
        detail=str(exc) if settings.log_level == "DEBUG" else None,
        timestamp=datetime.now(UTC).isoformat(),
        request_id=getattr(getattr(request, "state", object()), "request_id", None)
        or request.headers.get("X-Request-ID"),
    )
    return JSONResponse(
        status_code=500, content=error_response.model_dump(exclude_none=True)
    )


@app.get("/", response_model=MainResponse)
async def root(
    request: Request,
    runtime_settings: Settings = Depends(get_settings),
    client: RedisClient | None = Depends(get_redis_client),
) -> MainResponse | JSONResponse:
    """Return main response with optional Redis data."""
    timestamp = datetime.now(UTC).isoformat()
    redis_data: str | None = "Redis unavailable"
    redis_error: str | None = None

    if client and runtime_settings.redis_enabled:
        try:
            key = "chaos_lab:data:sample"
            val = await client.get(key)
            if not val:
                val = f"Data created at {timestamp}"
                await client.set(key, val)
            redis_data = val
            # Reduce Redis ops: increment every 10th request deterministically
            global _request_counter
            _request_counter += 1
            if _request_counter % 10 == 0:
                await client.increment("chaos_lab:counter:requests")
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).error("Redis operation failed: %s", e)
            redis_error = str(e)

    # Emit custom metrics (best-effort)
    with suppress(Exception):
        from app.telemetry import record_redis_metrics  # local import to avoid cycles

        connected = (
            client is not None
            and redis_error is None
            and runtime_settings.redis_enabled
        )
        # Latency unknown here; capture as 0 when not measured on this path
        record_redis_metrics(connected=connected, latency_ms=0)

    if runtime_settings.redis_enabled and redis_error:
        error_response = ErrorResponse(
            error="Service Unavailable",
            detail=f"Redis operation failed: {redis_error}",
            timestamp=timestamp,
            request_id=getattr(getattr(request, "state", object()), "request_id", None)
            or request.headers.get("X-Request-ID"),
        )
        return JSONResponse(
            status_code=503, content=error_response.model_dump(exclude_none=True)
        )

    return MainResponse(
        message="Hello from AKS Chaos Lab",
        redis_data=redis_data,
        timestamp=timestamp,
    )


@app.get("/health", response_model=HealthResponse)
async def health(
    request: Request,
    runtime_settings: Settings = Depends(get_settings),
    client: RedisClient | None = Depends(get_redis_client),
) -> HealthResponse | JSONResponse:
    """Return health status including Redis connectivity."""
    if _is_health_cache_valid() and isinstance(
        _health_cache.get("payload"), HealthResponse
    ):
        cached_resp = _health_cache["payload"]
        assert isinstance(cached_resp, HealthResponse)
        cached_code = int(_health_cache.get("status_code") or 200)
        if cached_code != 200:
            return JSONResponse(
                status_code=cached_code, content=cached_resp.model_dump()
            )
        return cached_resp

    redis_connected = False
    redis_latency_ms = 0

    # If Redis is disabled or host is unset, skip connection and treat as healthy
    if not runtime_settings.redis_enabled or not runtime_settings.redis_host:
        status = "healthy"
        resp = HealthResponse(
            status=status,
            redis={"connected": False, "latency_ms": 0},
            timestamp=datetime.now(UTC).isoformat(),
        )
        with suppress(Exception):
            from app.telemetry import record_redis_metrics

            record_redis_metrics(connected=False, latency_ms=0)
        _update_health_cache(resp, 200)
        return resp

    if client and runtime_settings.redis_enabled:
        try:
            start = asyncio.get_event_loop().time()
            await client.ping()
            end = asyncio.get_event_loop().time()
            redis_connected = True
            redis_latency_ms = int((end - start) * 1000)
        except Exception:
            redis_connected = False

    status = "healthy" if redis_connected else "unhealthy"
    resp = HealthResponse(
        status=status,
        redis={"connected": redis_connected, "latency_ms": redis_latency_ms},
        timestamp=datetime.now(UTC).isoformat(),
    )
    code = 200 if status == "healthy" else 503
    # Emit custom metrics with measured latency
    with suppress(Exception):
        from app.telemetry import record_redis_metrics

        record_redis_metrics(connected=redis_connected, latency_ms=redis_latency_ms)
    _update_health_cache(resp, code)
    if code != 200:
        return JSONResponse(status_code=code, content=resp.model_dump())
    return resp
