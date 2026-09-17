"""Rate limit e idempotência do webhook do Asaas (via Redis).

- Fail-open: se o Redis estiver indisponível, o webhook NÃO é bloqueado
  (evita derrubar o fluxo de pagamento por infraestrutura).
- Idempotência: o mesmo evento + resource id só é processado uma vez.
"""
import aioredis

from app.core.config import get_settings

redis_client = aioredis.from_url(get_settings().redis_url, decode_responses=True)

# Limite de eventos/minuto do webhook Asaas.
ASAAS_RATE_LIMIT_PER_MINUTE = 120
# TTL da chave de idempotência (24h).
IDEMPOTENCY_TTL = 86400

async def asaas_rate_limit() -> bool:
    """Limite de eventos/minuto do webhook Asaas (fail-open)."""
    try:
        key = "rl:asaas:webhook"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 60)
        return count <= ASAAS_RATE_LIMIT_PER_MINUTE
    except Exception:
        return True

async def asaas_idempotency(event: str, resource_id: str) -> bool:
    """Proteção contra replay: mesmo evento + resource id só é aceito uma vez."""
    if not resource_id:
        return True  # sem id, não dá para deduplicar — processa (fail-open)
    try:
        key = f"idem:asaas:{event}:{resource_id}"
        return bool(await redis_client.set(key, "1", nx=True, ex=IDEMPOTENCY_TTL))
    except Exception:
        return True