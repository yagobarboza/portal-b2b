"""Recuperação de senha (seção 12 do doc).

- Token criptograficamente seguro, expiração, uso único.
- Mensagem genérica: nunca revela se o e-mail existe.
- Invalida sessões após a troca.
"""
import secrets
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.redis_settings import create_redis_client

settings = get_settings()

_redis: Redis | None = None

RESET_TTL_SECONDS = 3600  # 1 hora

def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = create_redis_client(settings)
    return _redis

async def create_reset_token(user_id: UUID) -> str:
    """Gera um token de reset de uso único com expiração."""
    r = _get_redis()
    token = secrets.token_urlsafe(32)
    await r.set(f"auth:reset:{token}", str(user_id), ex=RESET_TTL_SECONDS)
    return token

async def consume_reset_token(token: str) -> UUID | None:
    """Consome o token (uso único). Retorna o user_id ou None se inválido/expirado."""
    r = _get_redis()
    key = f"auth:reset:{token}"
    user_id = await r.get(key)
    if user_id is None:
        return None
    await r.delete(key)  # uso único
    return UUID(user_id)
