"""Bloqueio progressivo de login (seção 8 do doc).

Registra tentativas falhas por e-mail/IP no Redis e aplica
bloqueio progressivo: quanto mais falhas, maior o tempo de
bloqueio. Mensagens ao cliente são sempre genéricas.
"""
import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()

_redis: aioredis.Redis | None = None

def _get_redis() -> aioredis.Redis:
    """Cliente Redis do guard (lazy, compartilhado)."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis

def _counter_key(email: str | None, ip: str | None) -> str:
    ident = (email or "").lower() if email else (ip or "unknown")
    return f"login:fail:{ident}"

def _lock_key(email: str | None, ip: str | None) -> str:
    ident = (email or "").lower() if email else (ip or "unknown")
    return f"login:block:{ident}"

def _block_seconds(failures: int) -> int:
    """Tempo de bloqueio progressivo (segundos).

    - Abaixo do limite: 0 (sem bloqueio)
    - 1ª vez no limite: 5 min
    - 2ª vez no limite: 30 min
    - 3ª vez ou mais: 12 horas
    """
    threshold = settings.LOGIN_BLOCK_THRESHOLD
    if failures < threshold:
        return 0
    level = failures // threshold
    if level == 1:
        return settings.LOGIN_BLOCK_SECONDS
    if level == 2:
        return settings.LOGIN_BLOCK_SECONDS * 6  # 30 min
    return 3600 * 12  # 12 h

async def register_failed_attempt(email: str | None, ip: str | None) -> int:
    """Registra uma tentativa falha e aplica bloqueio se necessário.

    Retorna a contagem atual de falhas.
    """
    r = _get_redis()
    key = _counter_key(email, ip)
    failures = int(await r.incr(key))
    await r.expire(key, 3600 * 24)  # janela de contagem: 24h
    secs = _block_seconds(failures)
    if secs:
        # Reaplica o bloqueio com TTL (falhas contínuas estendem o bloqueio)
        await r.set(_lock_key(email, ip), "1", ex=secs)
    return failures

async def is_locked(email: str | None, ip: str | None) -> tuple[bool, int]:
    """Retorna (bloqueado?, segundos_restantes)."""
    r = _get_redis()
    ttl = await r.ttl(_lock_key(email, ip))
    if ttl is None or ttl == -2:  # chave não existe
        return False, 0
    return True, max(int(ttl), 1)

async def clear_attempts(email: str | None, ip: str | None) -> None:
    """Limpa contagem e bloqueio (login bem-sucedido)."""
    r = _get_redis()
    await r.delete(_counter_key(email, ip))
    await r.delete(_lock_key(email, ip))

async def check_rate_limit(ip: str, *, limit: int, window: int) -> bool:
    """Rate limit por IP (seção 8).

    Retorna True se o IP excedeu `limit` requisições na janela de
    `window` segundos. Usa Redis — compartilhado entre instâncias.
    """
    r = _get_redis()
    key = f"login:rl:{ip}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window)
    return count > limit