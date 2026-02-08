import pytest

from app.config import Settings
from app.redis_client import RedisClient


@pytest.mark.asyncio
async def test_redis_client_not_connected_errors() -> None:
    client = RedisClient(
        "localhost", 6379, Settings(redis_enabled=True, redis_ssl=False)  # ty: ignore[unknown-argument]
    )
    with pytest.raises(RuntimeError):
        await client.get("k")
    with pytest.raises(RuntimeError):
        await client.set("k", "v")
    with pytest.raises(RuntimeError):
        await client.increment("k")
    with pytest.raises(RuntimeError):
        await client.ping()
