"""Jobs de PULL da API do cliente (Bloco B4 — tipo `api`).

- `pull_integration_job`: executa o pull de UMA integração (enfileirado).
- `schedule_pull_integrations`: cron que roda a cada minuto, encontra as
  integrações `api` ativas cujo intervalo venceu e enfileira o pull.

Cada job abre a PRÓPRIA sessão de banco (mesmo padrão de worker/jobs.py).
"""
import logging
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.queue import enqueue_job
from app.database.session import async_session_factory
from app.repositories.integration import IntegrationRepository
from app.services.api_pull import fetch_and_apply_stock

logger = logging.getLogger("pull_jobs")

settings = get_settings()
_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

async def _claim_pull(integration_id: UUID, interval_minutes: int) -> bool:
    """True se o pull está 'devido' (janela de intervalo passou). Fail-open."""
    try:
        key = f"pull:last:{integration_id}"
        return bool(
            await _redis.set(key, "1", nx=True, ex=interval_minutes * 60)
        )
    except Exception:  # noqa: BLE001
        logger.warning("Redis indisponível — agendamento de pull liberado.")
        return True

async def pull_integration_job(ctx: dict, *, integration_id: str) -> None:
    """Executa o pull de estoque de uma integração `api` em background."""
    async with async_session_factory() as db:
        try:
            repo = IntegrationRepository(db)
            integration = await repo.get(UUID(integration_id))
            if integration and integration.is_active and integration.type == "api":
                config = await repo.get_api_config(integration)
                if config:
                    await fetch_and_apply_stock(db, integration=integration, config=config)
                    await db.commit()
        finally:
            await db.close()

async def schedule_pull_integrations(ctx: dict) -> None:
    """Cron (a cada minuto): enfileira o pull das integrações `api` devidas.

    O controle de 'devido' usa Redis (chave com TTL = intervalo), então não
    grava nada no banco só para agendar. Erros de agendamento são logados e
    não derrubam o cron.
    """
    async with async_session_factory() as db:
        try:
            repo = IntegrationRepository(db)
            integrations = await repo.list_active_by_type("api")
            for integration in integrations:
                config = await repo.get_api_config(integration)
                interval = (config or {}).get("interval_minutes", 15)
                if await _claim_pull(integration.id, int(interval)):
                    await enqueue_job(
                        "pull_integration_job",
                        integration_id=str(integration.id),
                    )
        finally:
            await db.close()