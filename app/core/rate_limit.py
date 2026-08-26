"""Rate limiting do Portal B2B.

1) limiter (slowapi) — usado pelo main.py (app.state.limiter) para
   limites globais por IP (ex.: login) via decorator @limiter.limit(...).
2) check_rate_limit (Redis, Bloco 17) — rate limit distribuído que
   funciona com MÚLTIPLAS instâncias (estado no Redis, não em memória).
   Fail-open: se o Redis cair, a requisição passa (não derruba o serviço).
   Padrão: INCR + EXPIRE (atômico) — janela deslizante simples.
"""
import logging

import redis.asyncio as aioredis
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ===== Limiter global do slowapi (usado pelo main.py) =====
limiter = Limiter(key_func=get_remote_address)

# ===== Rate limit distribuído via Redis (Bloco 17) =====
_redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)

async def check_rate_limit(key: str, limit: int, window: int) -> bool:
    """Incrementa o contador e retorna True se a requisição deve ser BLOQUEADA.

    Args:
        key: identificador do limite (ex.: 'domain:fiobikeshop.com.br')
        limit: número máximo de requisições por janela
        window: tamanho da janela em segundos

    Fail-open: se o Redis falhar, retorna False (não bloqueia).
    """
    try:
        rkey = f"rl:{key}"
        n = await _redis.incr(rkey)
        if n == 1:
            await _redis.expire(rkey, window)
        return n > limit
    except Exception:  # noqa: BLE001
        logger.exception("Rate limit check failed for %s", key)
        return False