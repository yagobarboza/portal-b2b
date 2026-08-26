"""Utilitário de fila: a API enfileira jobs no Redis (ARQ).

A API NÃO executa trabalho pesado — apenas publica o job na fila
e devolve a resposta imediatamente. O worker processa em background.
Fail-open: se o Redis estiver fora, registra log e NÃO derruba o request.
"""
import logging

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

async def enqueue_job(function: str, *args, **kwargs) -> bool:
    """Enfileira um job no Redis. Retorna True se enfileirou.

    - function: nome da função registrada no worker (ex.: 'send_invite_email_job')
    - *args/**kwargs: parâmetros do job (keyword-only na assinatura do job)
    """
    pool = None
    try:
        pool = await create_pool(_redis_settings)
        await pool.enqueue_job(function, *args, **kwargs)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enfileirar job %s", function)
        return False
    finally:
        if pool is not None:
            await pool.aclose()