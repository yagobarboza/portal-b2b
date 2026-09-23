"""Worker real de background jobs (ARQ + Redis).

Executa as funções enfileiradas pela API de forma assíncrona.
- Subir como serviço separado (worker de consumo).
- Retry automático com backoff exponencial.
- Tarefas periódicas rodam exclusivamente em ``worker.scheduler``.
"""
import asyncio

from arq import Worker

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.monitoring import init_sentry
from app.core.redis_settings import arq_redis_settings
from worker.jobs import (
    process_inbox_job,
    process_stock_file_job,
    run_sync_job,
    run_full_sync_job,
    send_invite_email_job,
    send_notification_job,
)
from worker.pull_jobs import (
    pull_integration_job,
)
from worker.runtime import metrics_port

settings = get_settings()
setup_logging()
init_sentry()
logger = get_logger("worker")

# Property real do config.py: redis_url (usa REDIS_URL se definido)
REDIS_SETTINGS = arq_redis_settings(settings)

async def startup(ctx: dict) -> None:
    ctx["started_at"] = asyncio.get_event_loop().time()
    try:
        from prometheus_client import start_http_server

        port = metrics_port()
        start_http_server(port)
        logger.info("worker_metrics_started", port=port)
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
