"""Azure Managed Redis client with Entra ID authentication.

Uses redis-entraid credential_provider for automatic token management:
- Automatic token refresh before expiry
- Built-in re-authentication handling
- Object ID extraction from token for Redis AUTH

Reference:
- https://learn.microsoft.com/en-us/azure/redis/python-get-started
- https://learn.microsoft.com/en-us/azure/redis/entra-for-authentication
"""

import logging
import time
from contextlib import suppress
from typing import Any, cast

import redis.asyncio as aioredis
from redis.asyncio.client import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis_entraid.cred_provider import create_from_default_azure_credential

from app.config import Settings
from app.telemetry import record_redis_metrics

logger = logging.getLogger(__name__)


class RedisClient:
    """Azure Managed Redis client with Entra ID authentication.

    Uses redis-entraid credential_provider for:
    - Automatic token acquisition via DefaultAzureCredential
    - Automatic token refresh before expiry
    - Object ID extraction from token for Redis AUTH username

    Note: AZURE_CLIENT_ID environment variable is still required for
    DefaultAzureCredential to select the correct User-Assigned Managed Identity.
    """

    _SCOPE = ("https://redis.azure.com/.default",)

    def __init__(self, host: str, port: int, settings: Settings) -> None:
        self._host = host
        self._port = port
        self._settings = settings
        self._client: Redis | None = None
        self._credential_provider: Any = None

    def _build_client(self) -> Redis:
        """Build Redis client with credential_provider for Entra ID auth.

        The credential_provider handles:
        - Token acquisition using DefaultAzureCredential
        - Automatic token refresh before expiry
        - Extracting Object ID from token for Redis AUTH username
        """
        retry = Retry(
            ExponentialBackoff(
                base=self._settings.redis_backoff_base,
                cap=self._settings.redis_backoff_cap,
            ),
            retries=self._settings.redis_max_retries,
        )

        # redis-entraid handles token acquisition, refresh, and auth automatically
        # DefaultAzureCredential will use AZURE_CLIENT_ID env var to select UAMI
        self._credential_provider = create_from_default_azure_credential(self._SCOPE)

        client = aioredis.Redis(
            host=self._host,
            port=self._port,
            ssl=self._settings.redis_ssl,
            credential_provider=self._credential_provider,
            socket_timeout=self._settings.redis_socket_timeout,
            socket_connect_timeout=self._settings.redis_socket_connect_timeout,
            max_connections=self._settings.redis_max_connections,
            retry=retry,
            retry_on_error=[RedisConnectionError, RedisTimeoutError],
            health_check_interval=30,
            decode_responses=True,
        )
        return client

    async def connect(self) -> None:
        """Connect to Redis and verify connectivity."""
        self._client = self._build_client()
        await self._client.ping()  # ty: ignore[invalid-await]

    async def reset_connections(self) -> int:
        """Forcefully close all connections in the pool.

        Returns number of closed connections (best-effort).
        """
        if not self._client:
            return 0
        count = 0
        try:
            pool = self._client.connection_pool
            await pool.disconnect(inuse_connections=True)
            count = 1
        except Exception as e:  # noqa: BLE001
            logger.debug("reset_connections failed: %s", e)
        return count

    async def close(self) -> None:
        """Close Redis client and cleanup resources."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._credential_provider = None

    async def get(self, key: str) -> str | None:
        """Get value by key."""
        if not self._client:
            raise RuntimeError("Redis client is not connected")
        res = await self._client.get(key)
        return cast(str | None, res)

    async def set(self, key: str, value: str) -> None:
        """Set key-value pair."""
        if not self._client:
            raise RuntimeError("Redis client is not connected")
        await self._client.set(key, value)

    async def increment(self, key: str) -> int:
        """Increment key value."""
        if not self._client:
            raise RuntimeError("Redis client is not connected")
        val = await self._client.incr(key)
        return int(cast(int, val))

    async def ping(self) -> bool:
        """Ping Redis and record metrics."""
        if not self._client:
            raise RuntimeError("Redis client is not connected")
        start = time.time()
        try:
            res = await self._client.ping()  # ty: ignore[invalid-await]
            latency_ms = int((time.time() - start) * 1000)
            with suppress(Exception):
                record_redis_metrics(True, latency_ms)
            return bool(res)
        except Exception:
            with suppress(Exception):
                record_redis_metrics(False, -1)
            raise
