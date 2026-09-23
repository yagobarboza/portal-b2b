"""Agendador legado para desenvolvimento local (APScheduler).

So roda quando ``API_SCHEDULER_ENABLED=true``. Em produção, o processo
``worker.scheduler`` executa as tarefas periódicas fora da API.

✅ async_session_factory — confirmado em app/database/session.py.
"""
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database.session import async_session_factory
from app.services.billing_block import block_overdue_companies

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

async def _run_block_overdue_job() -> None:
    """Executa o bloqueio de empresas inadimplentes em uma sessão própria."""
    async with async_session_factory() as db:
        try:
            blocked = await block_overdue_companies(db)
            if blocked:
                logger.info("Job de bloqueio: %d empresa(s) bloqueada(s).", len(blocked))
        except Exception:  # noqa: BLE001
            logger.exception("Falha no job de bloqueio por não pagamento.")

def start_scheduler() -> None:
    """Registra os jobs e inicia o agendador (idempotente)."""
    if scheduler.running:
        return  # evita "Scheduler already running" com --reload / múltiplos create_app()
    scheduler.add_job(
        _run_block_overdue_job,
        CronTrigger(hour=3, minute=0, timezone="America/Sao_Paulo"),
        id="block_overdue_companies",
        replace_existing=True,
        max_instances=1,       # evita execuções sobrepostas
        coalesce=True,         # se atrasou, roda só a última vez
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info("Scheduler iniciado — job diário de bloqueio às 03:00 BRT.")

def shutdown_scheduler() -> None:
    """Encerra o agendador (best-effort)."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler encerrado.")

@asynccontextmanager
async def scheduler_lifespan(app):
    """Lifespan que liga/desliga o scheduler junto com a API."""
    from app.core.config import get_settings

    if not get_settings().API_SCHEDULER_ENABLED:
        logger.info("Scheduler da API desabilitado; use worker.scheduler.")
        yield
        return
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()
