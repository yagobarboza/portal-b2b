from typing import AsyncGenerator

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.redis_settings import create_redis_client

settings = get_settings()

# Cliente Redis compartilhado (async).
# Usado para: cache, rate limit, Pub/Sub, WebSockets (seções 25 e 39 do doc).
redis_client = create_redis_client(settings)

async def get_redis() -> AsyncGenerator[Redis, None]:
    """Dependency do FastAPI: fornece o cliente Redis."""
    yield redis_client
