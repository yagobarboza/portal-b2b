"""Scheduler persistido de capabilities e reconciliação."""

import random
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.queue import enqueue_job
from app.core.logging import get_logger
from app.database.session import async_session_factory
from app.repositories.integration import IntegrationRepository

logger = get_logger("integration_scheduler")
SCHEDULE_CLAIM_LIMIT = 100


async def pull_integration_job(
    ctx: dict, *, integration_id: str, run_id: str | None = None
) -> None:
    """Compatibilidade com jobs antigos; usa o runner com retry classificado."""
    from worker.jobs import run_sync_job

    await run_sync_job(
        ctx,
        integration_id=integration_id,
        entity="stock",
        run_id=run_id,
    )


async def schedule_pull_integrations(ctx: dict) -> None:
    """Reivindica schedules vencidos com SKIP LOCKED e avança antes do enqueue."""
    dispatches: list[tuple[str, str, str, str]] = []
    async with async_session_factory() as db:
        repo = IntegrationRepository(db)
        schedules = await repo.claim_due_schedules(limit=SCHEDULE_CLAIM_LIMIT)
        now = datetime.now(timezone.utc)
        for schedule in schedules:
            integration = await repo.get(schedule.integration_id)
            if integration is None:
                schedule.is_active = False
                continue
            jitter = random.randint(0, max(0, schedule.jitter_seconds))
            schedule.last_enqueued_at = now
            schedule.next_run_at = now + timedelta(
                seconds=schedule.interval_seconds + jitter
            )
            entity = (
                "reconciliation"
                if schedule.capability == "reconciliation"
                else schedule.capability
            )
            run = await repo.create_sync(
                integration.id,
                integration.tenant_id,
                entity,
                trigger="scheduled",
                correlation_id=str(schedule.id),
                run_metadata={"schedule_id": str(schedule.id)},
            )
            run.cursor = schedule.cursor
            dispatches.append(
                (str(integration.id), entity, str(run.id), str(schedule.id))
            )
        # Run e próximo vencimento são atômicos: dois workers não agendam em dobro.
        await db.commit()

    for integration_id, entity, run_id, schedule_id in dispatches:
        enqueued = await enqueue_job(
            "run_sync_job",
            integration_id=integration_id,
            entity=entity,
            run_id=run_id,
            schedule_id=schedule_id,
        )
        if not enqueued:
            # Reabre o schedule rapidamente; a execução continua visível no histórico.
            async with async_session_factory() as db:
                repo = IntegrationRepository(db)
                run = await repo.get_sync(UUID(run_id))
                schedule = await repo.get_schedule(
                    UUID(integration_id),
                    "reconciliation" if entity == "reconciliation" else entity,
                )
                if run:
                    from app.services.integration_observability import set_run_failure

                    set_run_failure(
                        run,
                        status="failed",
                        code="queue_unavailable",
                        retryable=True,
                        message="Fila indisponível; schedule reaberto para nova tentativa.",
                    )
                if schedule:
                    schedule.next_run_at = datetime.now(timezone.utc) + timedelta(
                        minutes=1
                    )
                await db.commit()
