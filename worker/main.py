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
from app.core.logging import get_logger, setup_logging
from app.core.monitoring import init_sentry
from worker.jobs import (
    dispatch_pending_files,
    dispatch_pending_inbox,
    dispatch_pending_runs,
    enforce_integration_retention_job,
    process_inbox_job,
    process_stock_file_job,
    run_sync_job,
    run_full_sync_job,
    refresh_integration_alerts_job,
    send_invite_email_job,
    send_notification_job,
)
from worker.pull_jobs import (
    pull_integration_job,
    schedule_pull_integrations,
)

settings = get_settings()
setup_logging()
init_sentry()
logger = get_logger("worker")

# Property real do config.py: redis_url (usa REDIS_URL se definido)
REDIS_SETTINGS = RedisSettings.from_dsn(settings.redis_url)

async def startup(ctx: dict) -> None:
    ctx["started_at"] = asyncio.get_event_loop().time()
    try:
        from prometheus_client import start_http_server

        start_http_server(settings.WORKER_METRICS_PORT)
        logger.info("worker_metrics_started", port=settings.WORKER_METRICS_PORT)
    except Exception:  # noqa: BLE001
        logger.exception("worker_metrics_start_failed")
    logger.info("worker_started")

async def shutdown(ctx: dict) -> None:
    logger.info("worker_stopped")

async def main() -> None:
    worker = Worker(
        functions=[
            send_invite_email_job,
            run_sync_job,
            run_full_sync_job,
            send_notification_job,
            process_inbox_job,
            process_stock_file_job,
            pull_integration_job,
        ],
        cron_jobs=[
            # ✅ BLOCO B4: a cada minuto, enfileira o pull das integrações `api`.
            # ⚠️ ARQ NÃO aceita "minute='*'" (isso levanta RuntimeError: *).
            # Omitir os argumentos de tempo equivale a "*" (todo valor);
            # `second` já é 0 por padrão → roda no segundo 0 de cada minuto.
            cron(schedule_pull_integrations),
            cron(dispatch_pending_inbox, second={10, 40}),
            cron(dispatch_pending_files, second={20, 50}),
            cron(dispatch_pending_runs, second={5, 35}),
            cron(refresh_integration_alerts_job, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
            cron(enforce_integration_retention_job, hour=4, minute=15),
        ],
        redis_settings=REDIS_SETTINGS,
        on_startup=startup,
        on_shutdown=shutdown,
        max_tries=5,
        job_timeout=300,
        keep_result=3600,
    )
    try:
        await worker.async_run()
    except asyncio.CancelledError:
        # Encerramento normal por SIGTERM durante deploy/restart.
        logger.info("worker_shutdown_cancelled")

if __name__ == "__main__":
    asyncio.run(main())
