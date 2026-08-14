from typing import AsyncGenerator

from redis.asyncio import Redis

from app.core.config import get_settings

settings = get_settings()

# Cliente Redis compartilhado (async).
# Usado para: cache, rate limit, Pub/Sub, WebSockets (seções 25 e 39 do doc).
redis_client = Redis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)

async def get_redis() -> AsyncGenerator[Redis, None]:
    """Dependency do FastAPI: fornece o cliente Redis."""
    yield redis_client