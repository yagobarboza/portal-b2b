"""Worker real de background jobs (ARQ + Redis).

Executa as funções enfileiradas pela API de forma assíncrona.
- Subir no Render como serviço separado (worker).
- Retry automático com backoff exponencial.
- BLOCO B4: cron de 1 minuto agenda o pull das integrações `api` devidas.
"""
import asyncio

from arq import Worker, cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from worker.jobs import (
    run_sync_job,
    send_invite_email_job,
    send_notification_job,
)
from worker.pull_jobs import (
    pull_integration_job,
    schedule_pull_integrations,
)

settings = get_settings()

# Property real do config.py: redis_url (usa REDIS_URL se definido)
REDIS_SETTINGS = RedisSettings.from_dsn(settings.redis_url)

async def startup(ctx: dict) -> None:
    ctx["started_at"] = asyncio.get_event_loop().time()
    print("[worker] Iniciado.")

async def shutdown(ctx: dict) -> None:
    print("[worker] Encerrado.")

async def main() -> None:
    worker = Worker(
        functions=[
            send_invite_email_job,
            run_sync_job,
            send_notification_job,
            pull_integration_job,
        ],
        cron_jobs=[
            # ✅ BLOCO B4: a cada minuto, enfileira o pull das integrações `api`.
            # ⚠️ ARQ NÃO aceita "minute='*'" (isso levanta RuntimeError: *).
            # Omitir os argumentos de tempo equivale a "*" (todo valor);
            # `second` já é 0 por padrão → roda no segundo 0 de cada minuto.
            cron(schedule_pull_integrations),
        ],
        redis_settings=REDIS_SETTINGS,
        on_startup=startup,
        on_shutdown=shutdown,
        max_tries=5,
        job_timeout=300,
        keep_result=3600,
    )
    await worker.async_run()

if __name__ == "__main__":
    asyncio.run(main())