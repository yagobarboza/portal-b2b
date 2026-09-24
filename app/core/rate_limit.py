"""Rate limiting do Portal B2B.

1) limiter (slowapi) — usado pelo main.py (app.state.limiter) para
   limites globais por IP (ex.: login) via decorator @limiter.limit(...).
2) check_rate_limit (Redis, Bloco 17) — rate limit distribuído que
   funciona com MÚLTIPLAS instâncias (estado no Redis, não em memória).
   Fail-open: se o Redis cair, a requisição passa (não derruba o serviço).
   Padrão: INCR + EXPIRE (atômico) — janela deslizante simples.
3) asaas_rate_limit / asaas_idempotency — proteção do webhook do Asaas:
   limite de eventos por minuto + deduplicação de eventos (anti-replay).
   Também fail-open: se o Redis falhar, o webhook não é bloqueado.
"""
import logging

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.core.redis_settings import create_redis_client

logger = logging.getLogger(__name__)

# ===== Limiter global do slowapi (usado pelo main.py) =====
limiter = Limiter(key_func=get_remote_address)

# ===== Rate limit distribuído via Redis (Bloco 17) =====
_redis = create_redis_client(get_settings())

# ===== Limites do webhook do Asaas =====
# Eventos por minuto aceitos no webhook do Asaas.
ASAAS_RATE_LIMIT_PER_MINUTE = 120
# TTL da chave de idempotência (24h) — proteção contra replay.
IDEMPOTENCY_TTL = 86400

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

async def asaas_rate_limit() -> bool:
    """Limite de eventos/minuto do webhook do Asaas (fail-open).

    Retorna True se a requisição pode prosseguir; False se excedeu o limite.
    """
    try:
        key = "rl:asaas:webhook"
        count = await _redis.incr(key)
        if count == 1:
            await _redis.expire(key, 60)
        return count <= ASAAS_RATE_LIMIT_PER_MINUTE
    except Exception:  # noqa: BLE001
        logger.exception("Rate limit check failed for asaas webhook")
        return True

async def asaas_idempotency(event: str, resource_id: str) -> bool:
    """Proteção contra replay: mesmo evento + resource id só é aceito uma vez.

    Retorna True se é a primeira vez (deve processar); False se já processado.
    """
    if not resource_id:
        return True  # sem id, não dá para deduplicar — processa (fail-open)
    try:
        key = f"idem:asaas:{event}:{resource_id}"
        return bool(await _redis.set(key, "1", nx=True, ex=IDEMPOTENCY_TTL))
    except Exception:  # noqa: BLE001
        logger.exception("Idempotency check failed for asaas webhook")
        return True
