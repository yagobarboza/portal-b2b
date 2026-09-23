"""Processo exclusivo para tarefas periodicas.

Usa uma fila ARQ separada. Assim, o scheduler nunca concorre com os workers
de consumo e a API pode escalar sem duplicar cron jobs.
"""

import asyncio
from zoneinfo import ZoneInfo

from arq import Worker, cron

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.monitoring import init_sentry
from app.core.redis_settings import arq_redis_settings
from worker.jobs import (
    dispatch_pending_files,
    dispatch_pending_inbox,
    dispatch_pending_runs,
    enforce_integration_retention_job,
    refresh_integration_alerts_job,
)
from worker.pull_jobs import schedule_pull_integrations
from worker.runtime import metrics_port
from worker.scheduled_jobs import block_overdue_companies_job

settings = get_settings()
setup_logging()
init_sentry()
logger = get_logger("scheduler_worker")


async def startup(ctx: dict) -> None:
    try:
        from prometheus_client import start_http_server

        port = metrics_port()
        start_http_server(port)
        logger.info("scheduler_metrics_started", port=port)
    except Exception:
        logger.exception("scheduler_metrics_start_failed")
    logger.info("scheduler_worker_started")


async def shutdown(ctx: dict) -> None:
    logger.info("scheduler_worker_stopped")


async def main() -> None:
    scheduler = Worker(
        functions=[],
        cron_jobs=[
            cron(schedule_pull_integrations, run_at_startup=True),
            cron(dispatch_pending_inbox, second={10, 40}, run_at_startup=True),
            cron(dispatch_pending_files, second={20, 50}, run_at_startup=True),
            cron(dispatch_pending_runs, second={5, 35}, run_at_startup=True),
            cron(
                refresh_integration_alerts_job,
                minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            ),
            cron(enforce_integration_retention_job, hour=4, minute=15),
            cron(block_overdue_companies_job, hour=3, minute=0),
        ],
        queue_name="arq:scheduler",
        redis_settings=arq_redis_settings(settings),
        on_startup=startup,
        on_shutdown=shutdown,
        max_jobs=1,
        max_tries=3,
        job_timeout=900,
        keep_result=3600,
        timezone=ZoneInfo("America/Sao_Paulo"),
    )
    try:
        await scheduler.async_run()
    except asyncio.CancelledError:
        logger.info("scheduler_shutdown_cancelled")


if __name__ == "__main__":
    asyncio.run(main())
