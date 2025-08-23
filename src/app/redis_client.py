import asyncio
import logging
import time
from contextlib import suppress
from typing import Any, cast

import redis.asyncio as aioredis
from azure.identity.aio import DefaultAzureCredential
from redis.asyncio.client import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import AuthenticationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import Settings
from app.telemetry import record_redis_metrics

logger = logging.getLogger(__name__)


class RedisClient:
    """Azure Managed Redis client with Entra ID authentication.

    - Uses DefaultAzureCredential (aio) to obtain access tokens for scope
      "https://redis.azure.com/.default".
    - Username must be the managed identity client id (AZURE_CLIENT_ID).
    - Handles token refresh and single retry on auth errors.
    """

    _SCOPE = "https://redis.azure.com/.default"
    _TOKEN_REFRESH_SAFETY = 120  # seconds before expiry to refresh

    def __init__(self, host: str, port: int, settings: Settings) -> None:
        self._host = host
        self._port = port
        self._settings = settings
        self._client: Redis | None = None
        self._credential: DefaultAzureCredential | None = None
        self._access_token: str | None = None
        self._access_token_exp: float = 0.0
        self._token_lock = asyncio.Lock()

    async def _get_token(self) -> str:
        """Get a valid access token, refreshing if near expiry.

        Returns the token string to be used as Redis password.
        """
        async with self._token_lock:
            now = time.time()
            if self._access_token and now < (
                self._access_token_exp - self._TOKEN_REFRESH_SAFETY
            ):
                return self._access_token

            if not self._credential:
                # DefaultAzureCredential honors AZURE_CLIENT_ID to pick UAMI
                self._credential = DefaultAzureCredential()

            token = await self._credential.get_token(self._SCOPE)
            self._access_token = token.token
            # azure-identity returns AccessToken with expires_on epoch seconds
            self._access_token_exp = float(token.expires_on or (now + 300))
            return self._access_token

    async def _build_client(self) -> Redis:
        retry = Retry(
            ExponentialBackoff(
                base=self._settings.redis_backoff_base,
                cap=self._settings.redis_backoff_cap,
            ),
            retries=self._settings.redis_max_retries,
        )
        username = self._settings.azure_client_id
        if not username:
            raise RuntimeError("AZURE_CLIENT_ID is required for Entra ID auth")

        password = await self._get_token()
        client = aioredis.Redis(  # type: ignore[call-overload]
            host=self._host,
            port=self._port,
            ssl=self._settings.redis_ssl,
            username=username,
            password=password,
            socket_timeout=self._settings.redis_socket_timeout,
            socket_connect_timeout=self._settings.redis_socket_connect_timeout,
            max_connections=self._settings.redis_max_connections,
            retry=retry,
            retry_on_error=[RedisConnectionError, RedisTimeoutError],
            health_check_interval=30,
            decode_responses=True,
        )
        return cast(Redis, client)

    async def connect(self) -> None:
        self._client = await self._build_client()
        await self._client.ping()

    async def reset_connections(self) -> int:
        """Forcefully close all connections in the pool.

        Returns number of closed connections (best-effort).
        """
        if not self._client:
            return 0
        count = 0
        try:
            pool = self._client.connection_pool
            # redis-py doesn't expose a direct count; disconnect and return 1 as a signal
            await pool.disconnect(inuse_connections=True)
            count = 1
        except Exception as e:  # noqa: BLE001
            logger.debug("reset_connections failed: %s", e)
        return count

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()  # type: ignore[attr-defined]
            self._client = None
        if self._credential:
            await self._credential.close()
            self._credential = None

    async def _with_auth_retry(self, func: str, *args: Any, **kwargs: Any) -> Any:
        if not self._client:
            raise RuntimeError("Redis client is not connected")
        try:
            return await getattr(self._client, func)(*args, **kwargs)
        except AuthenticationError:
            # refresh token and rebuild client, then retry once
            await asyncio.sleep(0.1)
            self._access_token = None
            self._access_token_exp = 0.0
            self._client = await self._build_client()
            return await getattr(self._client, func)(*args, **kwargs)

    async def get(self, key: str) -> str | None:
        res = await self._with_auth_retry("get", key)
        return cast(str | None, res)

    async def set(self, key: str, value: str) -> None:
        await self._with_auth_retry("set", key, value)

    async def increment(self, key: str) -> int:
        val = await self._with_auth_retry("incr", key)
        return int(cast(int, val))

    async def ping(self) -> bool:
        start = time.time()
        try:
            res = await self._with_auth_retry("ping")
            latency_ms = int((time.time() - start) * 1000)
            with suppress(Exception):
                record_redis_metrics(True, latency_ms)
            return bool(res)
        except Exception:
            with suppress(Exception):
                record_redis_metrics(False, -1)
            raise
