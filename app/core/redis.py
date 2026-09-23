from typing import AsyncGenerator

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.redis_settings import redis_client_kwargs

settings = get_settings()

# Cliente Redis compartilhado (async).
# Usado para: cache, rate limit, Pub/Sub, WebSockets (seções 25 e 39 do doc).
redis_client = Redis.from_url(
    settings.redis_url,
    **redis_client_kwargs(settings),
)

async def get_redis() -> AsyncGenerator[Redis, None]:
    """Dependency do FastAPI: fornece o cliente Redis."""
    yield redis_client
