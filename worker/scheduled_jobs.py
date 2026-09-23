"""Tarefas periodicas que nao devem rodar dentro da API HTTP."""

from app.core.logging import get_logger
from app.database.session import async_session_factory
from app.services.billing_block import block_overdue_companies

logger = get_logger("scheduled_jobs")


async def block_overdue_companies_job(ctx: dict) -> None:
    """Bloqueia empresas inadimplentes em uma transacao propria."""
    async with async_session_factory() as db:
        try:
            blocked = await block_overdue_companies(db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("billing_block_job_failed")
            raise
    logger.info("billing_block_job_completed", blocked_count=len(blocked))
