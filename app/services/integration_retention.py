"""Retenção de payloads/eventos de integração com resumo durável do run."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.metrics import PAYLOADS_PURGED_TOTAL
from app.models import (
    IntegrationAlert,
    IntegrationImportFile,
    IntegrationInbox,
    SyncExecution,
    WebhookEvent,
)


async def enforce_integration_retention(db: AsyncSession) -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload_before = now - timedelta(days=max(1, settings.INTEGRATION_PAYLOAD_RETENTION_DAYS))
    event_before = now - timedelta(days=max(1, settings.INTEGRATION_EVENT_RETENTION_DAYS))
    run_before = now - timedelta(days=max(1, settings.INTEGRATION_RUN_RETENTION_DAYS))
    counts: dict[str, int] = {}

    inbox = await db.execute(
        update(IntegrationInbox)
        .where(
            IntegrationInbox.created_at < payload_before,
            IntegrationInbox.status.in_(("succeeded", "dead_letter")),
            IntegrationInbox.payload.is_not(None),
        )
        .values(payload=None, result=None, payload_purged_at=now)
    )
    webhook = await db.execute(
        update(WebhookEvent)
        .where(
            WebhookEvent.created_at < payload_before,
            WebhookEvent.payload.is_not(None),
        )
        .values(payload=None, payload_purged_at=now)
    )
    files = await db.execute(
        update(IntegrationImportFile)
        .where(
            IntegrationImportFile.created_at < payload_before,
            IntegrationImportFile.status.in_(("succeeded", "failed", "dead_letter")),
            IntegrationImportFile.content.is_not(None),
        )
        .values(content=None, payload_purged_at=now)
    )
    for kind, result in (("inbox", inbox), ("webhook", webhook), ("file", files)):
        count = max(0, result.rowcount or 0)
        counts[f"purged_{kind}"] = count
        if count:
            PAYLOADS_PURGED_TOTAL.labels(kind).inc(count)

    deleted_webhooks = await db.execute(
        delete(WebhookEvent).where(WebhookEvent.created_at < event_before)
    )
    deleted_inbox = await db.execute(
        delete(IntegrationInbox).where(
            IntegrationInbox.created_at < event_before,
            IntegrationInbox.status.in_(("succeeded", "dead_letter")),
        )
    )
    deleted_files = await db.execute(
        delete(IntegrationImportFile).where(
            IntegrationImportFile.created_at < event_before,
            IntegrationImportFile.status.in_(("succeeded", "failed", "dead_letter")),
        )
    )
    deleted_alerts = await db.execute(
        delete(IntegrationAlert).where(
            IntegrationAlert.status == "resolved",
            IntegrationAlert.resolved_at < event_before,
        )
    )
    deleted_runs = await db.execute(
        delete(SyncExecution).where(
            SyncExecution.terminal_at < run_before,
            SyncExecution.status.in_(("success", "partial", "failed", "dead_letter")),
        )
    )
    counts.update(
        deleted_webhooks=max(0, deleted_webhooks.rowcount or 0),
        deleted_inbox=max(0, deleted_inbox.rowcount or 0),
        deleted_files=max(0, deleted_files.rowcount or 0),
        deleted_alerts=max(0, deleted_alerts.rowcount or 0),
        deleted_runs=max(0, deleted_runs.rowcount or 0),
    )
    return counts
