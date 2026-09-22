"""Funções de job executadas pelo worker ARQ (background).

Regras:
- Cada job abre a PRÓPRIA sessão de banco (não usa Depends do FastAPI).
- Sessão sempre fechada no finally (evita vazamento de conexão).
- Erros viram retry automático do ARQ (max_tries definido no main.py).
"""
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from arq import Retry

from app.database.session import async_session_factory
from app.integrations.retry import classify_retry
from app.core.logging import get_logger
from app.core.redaction import redact_text
from app.integrations.metrics import (
    OPEN_ALERTS,
    QUEUE_DEPTH,
    observe_retry,
    observe_terminal_run,
)
from app.services.integration_observability import set_run_failure

logger = get_logger("integration_worker")


async def _refresh_alerts(db, integration) -> None:  # noqa: ANN001
    from app.services.integration_dashboard import evaluate_integration_alerts

    await db.flush()
    await evaluate_integration_alerts(db, integration)


def _capture_exception(exc: Exception) -> None:
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001
        pass

async def send_invite_email_job(
    ctx: dict,
    *,
    to_email: str,
    invite_url: str,
    company_name: str,
    expires_hours: int,
) -> bool:
    """Envia e-mail de convite em background (não bloqueia o request).

    send_invite_email já é fail-safe (nunca lança exceção).
    """
    from app.services.email import send_invite_email

    return await send_invite_email(
        to_email=to_email,
        invite_url=invite_url,
        company_name=company_name,
        expires_hours=expires_hours,
    )

async def run_sync_job(
    ctx: dict,
    *,
    integration_id: str,
    entity: str = "products",
    run_id: str | None = None,
    schedule_id: str | None = None,
) -> None:
    """Executa uma capability real usando o connector configurado."""
    from app.repositories.integration import IntegrationRepository
    from app.services.api_pull import fetch_and_apply_stock
    from app.services.connector_sync import fetch_and_apply_products

    async with async_session_factory() as db:
        repo = IntegrationRepository(db)
        run = await repo.get_sync(UUID(run_id), for_update=True) if run_id else None
        if run is not None:
            current_status = getattr(run.status, "value", run.status)
            if current_status in {
                "running",
                "success",
                "partial",
                "failed",
                "dead_letter",
            }:
                await db.commit()
                return
        integration = await repo.get(UUID(integration_id))
        if not integration or not integration.is_active:
            if run:
                set_run_failure(
                    run,
                    status="failed",
                    code="integration_unavailable",
                    message="Integração inexistente ou inativa.",
                    retryable=False,
                )
                await db.commit()
                observe_terminal_run(run)
            return

        config = await repo.get_api_config(integration)
        if not config:
            if run:
                set_run_failure(
                    run,
                    status="failed",
                    code="connector_not_configured",
                    message="Connector não configurado.",
                    retryable=False,
                )
                await _refresh_alerts(db, integration)
                await db.commit()
                observe_terminal_run(run)
            return

        schedule = (
            await repo.get_schedule_by_id(UUID(schedule_id)) if schedule_id else None
        )
        if schedule and schedule.cursor and "value" in schedule.cursor:
            config["_cursor"] = schedule.cursor["value"]
        if schedule:
            config["_max_records"] = schedule.max_batch_size

        if run is None:
            run = await repo.create_sync(
                integration.id, integration.tenant_id, entity, trigger="worker"
            )
            await db.commit()
        run.attempt_count += 1
        run.status = "running"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        run.last_attempt_at = datetime.now(timezone.utc)
        run.next_retry_at = None
        await db.commit()
        persisted_run_id = run.id
        logger.info(
            "integration_run_started",
            run_id=str(run.id),
            integration_id=str(integration.id),
            entity=entity,
            trigger=run.trigger,
            attempt=run.attempt_count,
        )
        try:
            if entity in {"stock", "reconciliation"}:
                result = await fetch_and_apply_stock(
                    db,
                    integration=integration,
                    config=config,
                    sync_execution=run,
                )
            elif entity in {"product", "products"}:
                result = await fetch_and_apply_products(
                    db,
                    integration=integration,
                    config=config,
                    sync_execution=run,
                )
            else:
                raise ValueError(f"Capability não suportada: {entity}")
            if schedule and "_next_cursor" in result:
                schedule.cursor = (
                    {"value": result["_next_cursor"]}
                    if result["_next_cursor"] is not None
                    else None
                )
                run.cursor = schedule.cursor
            await _refresh_alerts(db, integration)
            await db.commit()
            observe_terminal_run(run)
            logger.info(
                "integration_run_completed",
                run_id=str(run.id),
                integration_id=str(integration.id),
                entity=entity,
                status=str(getattr(run.status, "value", run.status)),
                processed=run.processed,
                errors=run.errors,
                duration_ms=run.duration_ms,
            )
        except Exception as exc:  # noqa: BLE001 - classificação central
            await db.rollback()
            failed = await repo.get_sync(persisted_run_id)
            if failed:
                decision = classify_retry(exc, attempt=failed.attempt_count)
                exhausted = failed.attempt_count >= failed.max_attempts
                if decision.retryable and not exhausted:
                    set_run_failure(
                        failed,
                        status="pending",
                        code=decision.reason,
                        exc=exc,
                        retryable=True,
                        message=f"Retry agendado: {decision.reason}.",
                    )
                    failed.next_retry_at = datetime.now(timezone.utc) + timedelta(
                        seconds=decision.delay_seconds
                    )
                    observe_retry(failed, decision.reason)
                else:
                    set_run_failure(
                        failed,
                        status="dead_letter" if exhausted else "failed",
                        code=decision.reason,
                        exc=exc,
                        retryable=decision.retryable,
                        message=f"Falha terminal: {decision.reason}.",
                    )
                    await _refresh_alerts(db, integration)
                await db.commit()
                logger.warning(
                    "integration_run_retry" if decision.retryable and not exhausted else "integration_run_failed",
                    run_id=str(failed.id),
                    integration_id=str(integration.id),
                    entity=entity,
                    attempt=failed.attempt_count,
                    retryable=decision.retryable and not exhausted,
                    retry_in_seconds=decision.delay_seconds if decision.retryable and not exhausted else None,
                    error_code=decision.reason,
                    error_class=exc.__class__.__name__,
                )
                if decision.retryable and not exhausted:
                    raise Retry(defer=decision.delay_seconds) from exc
                observe_terminal_run(failed)
                _capture_exception(exc)


async def run_full_sync_job(
    ctx: dict,
    *,
    integration_id: str,
    product_run_id: str,
    stock_run_id: str,
) -> None:
    """Executa catálogo antes do estoque para preservar a dependência por SKU."""
    await run_sync_job(
        ctx,
        integration_id=integration_id,
        entity="products",
        run_id=product_run_id,
    )
    async with async_session_factory() as db:
        from app.repositories.integration import IntegrationRepository

        repo = IntegrationRepository(db)
        product_run = await repo.get_sync(UUID(product_run_id))
        product_status = (
            str(getattr(product_run.status, "value", product_run.status))
            if product_run
            else "failed"
        )
        if product_status not in {"success", "partial"}:
            stock_run = await repo.get_sync(UUID(stock_run_id))
            if stock_run:
                set_run_failure(
                    stock_run,
                    status="failed",
                    code="product_sync_dependency_failed",
                    retryable=False,
                    message="Sync de estoque cancelada porque a sync de produtos falhou.",
                )
                await db.commit()
                observe_terminal_run(stock_run)
            logger.warning(
                "integration_full_sync_dependency_failed",
                integration_id=integration_id,
                product_run_id=product_run_id,
                stock_run_id=stock_run_id,
            )
            return
    await run_sync_job(
        ctx,
        integration_id=integration_id,
        entity="stock",
        run_id=stock_run_id,
    )


async def process_inbox_job(ctx: dict, *, inbox_id: str) -> None:
    """Processa uma entrada durável; duplicatas concorrentes não executam duas vezes."""
    from app.repositories.integration import IntegrationRepository
    from app.schemas.integration import StockSyncRequest
    from app.schemas.integration import StockSyncResult
    from app.services.integration import process_webhook_payload
    from app.services.stock_sync import apply_stock_sync

    item_id = UUID(inbox_id)
    async with async_session_factory() as db:
        repo = IntegrationRepository(db)
        item = await repo.get_inbox(item_id, for_update=True)
        if item is None or item.status in {"succeeded", "dead_letter"}:
            await db.commit()
            return
        now = datetime.now(timezone.utc)
        locked_at = item.locked_at
        if locked_at and locked_at.tzinfo is None:
            locked_at = locked_at.replace(tzinfo=timezone.utc)
        if item.status == "processing" and locked_at and now - locked_at < timedelta(minutes=5):
            await db.commit()
            return
        item.status = "processing"
        item.locked_at = now
        item.attempts += 1
        await db.commit()

        try:
            item = await repo.get_inbox(item_id)
            assert item is not None
            integration = await repo.get(item.integration_id)
            if integration is None or not integration.is_active:
                raise ValueError("Integração inexistente ou inativa.")
            if item.channel == "webhook":
                if item.payload is None:
                    raise ValueError("Payload expirado pela política de retenção.")
                event = await repo.get_webhook_event_by_idempotency(
                    integration.id, item.idempotency_key
                )
                if event is None:
                    raise ValueError("Evento de webhook não encontrado.")
                event.status = "processing"
                result = await process_webhook_payload(
                    db,
                    integration,
                    event,
                    dict(item.payload),
                    sync_execution=(await repo.get_sync(item.run_id)) if item.run_id else None,
                )
            elif item.channel == "agent":
                if item.payload is None:
                    raise ValueError("Payload expirado pela política de retenção.")
                request = StockSyncRequest.model_validate(item.payload)
                run = await repo.get_sync(item.run_id) if item.run_id else None
                if run:
                    run.attempt_count = item.attempts
                result = await apply_stock_sync(
                    db,
                    integration=integration,
                    items=request.items,
                    sync_execution=run,
                )
            else:
                raise ValueError(f"Canal de inbox não suportado: {item.channel}")
            item.status = "succeeded"
            item.result = (
                StockSyncResult(**result).model_dump(mode="json")
                if item.channel == "agent"
                else result
            )
            item.processed_at = datetime.now(timezone.utc)
            item.locked_at = None
            item.last_error = None
            run = await repo.get_sync(item.run_id) if item.run_id else None
            if run:
                await _refresh_alerts(db, integration)
            await db.commit()
            if run:
                observe_terminal_run(run)
                logger.info(
                    "integration_inbox_completed",
                    inbox_id=str(item.id),
                    run_id=str(run.id),
                    integration_id=str(integration.id),
                    channel=item.channel,
                    status=str(getattr(run.status, "value", run.status)),
                    duration_ms=run.duration_ms,
                )
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            item = await repo.get_inbox(item_id, for_update=True)
            if item is None:
                return
            decision = classify_retry(exc, attempt=item.attempts)
            exhausted = item.attempts >= item.max_attempts
            terminal = not decision.retryable or exhausted
            item.status = "dead_letter" if terminal else "retry"
            item.locked_at = None
            item.last_error = redact_text(
                f"{decision.reason}: {exc}", limit=1000
            )
            if terminal:
                item.processed_at = datetime.now(timezone.utc)
            else:
                item.available_at = datetime.now(timezone.utc) + timedelta(
                    seconds=decision.delay_seconds
                )
            if item.run_id:
                run = await repo.get_sync(item.run_id)
                if run:
                    set_run_failure(
                        run,
                        status=("dead_letter" if exhausted else (
                            "failed" if terminal else "pending"
                        )),
                        code=decision.reason,
                        exc=exc,
                        retryable=decision.retryable,
                        message=item.last_error,
                    )
                    run.next_retry_at = None if terminal else item.available_at
            if item.channel == "webhook":
                event = await repo.get_webhook_event_by_idempotency(
                    item.integration_id, item.idempotency_key
                )
                if event:
                    event.status = "dead_letter" if terminal else "failed"
                    event.error = item.last_error
                    event.processed_at = datetime.now(timezone.utc) if terminal else None
            await db.commit()
            if item.run_id and run:
                if terminal:
                    observe_terminal_run(run)
                else:
                    observe_retry(run, decision.reason)
            logger.warning(
                "integration_inbox_failed",
                inbox_id=str(item.id),
                run_id=str(item.run_id) if item.run_id else None,
                integration_id=str(item.integration_id),
                channel=item.channel,
                terminal=terminal,
                error_code=decision.reason,
                error_class=exc.__class__.__name__,
            )
            if terminal:
                _capture_exception(exc)
            if not terminal:
                raise Retry(defer=decision.delay_seconds) from exc


async def process_stock_file_job(ctx: dict, *, run_id: str) -> None:
    """Valida e aplica um arquivo já persistido pela API."""
    from app.repositories.integration import IntegrationRepository
    from app.services.stock_sync import apply_stock_import

    persisted_run_id = UUID(run_id)
    async with async_session_factory() as db:
        repo = IntegrationRepository(db)
        stored = await repo.get_import_file_by_run(persisted_run_id, for_update=True)
        run = await repo.get_sync(persisted_run_id)
        if (
            stored is None
            or run is None
            or stored.status in {"succeeded", "failed", "dead_letter", "processing"}
        ):
            await db.commit()
            return
        stored.status = "processing"
        stored.attempts += 1
        run.status = "running"
        run.attempt_count = stored.attempts
        run.started_at = run.started_at or datetime.now(timezone.utc)
        run.last_attempt_at = datetime.now(timezone.utc)
        run.next_retry_at = None
        await db.commit()
        try:
            stored = await repo.get_import_file_by_run(persisted_run_id)
            assert stored is not None
            if stored.content is None:
                raise ValueError("Payload do arquivo expirado pela política de retenção.")
            if hashlib.sha256(stored.content).hexdigest() != stored.checksum:
                raise ValueError("Checksum do arquivo não confere.")
            integration = await repo.get(stored.integration_id)
            if integration is None or not integration.is_active:
                raise ValueError("Integração inexistente ou inativa.")
            await apply_stock_import(
                db,
                integration=integration,
                filename=stored.filename,
                content=stored.content,
                sync_execution=run,
            )
            stored.status = "succeeded"
            stored.processed_at = datetime.now(timezone.utc)
            stored.error = None
            await _refresh_alerts(db, integration)
            await db.commit()
            observe_terminal_run(run)
            logger.info(
                "integration_file_completed",
                run_id=str(run.id),
                integration_id=str(integration.id),
                status=str(getattr(run.status, "value", run.status)),
                duration_ms=run.duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            stored = await repo.get_import_file_by_run(persisted_run_id, for_update=True)
            run = await repo.get_sync(persisted_run_id)
            if stored is None or run is None:
                return
            decision = classify_retry(exc, attempt=stored.attempts)
            exhausted = stored.attempts >= run.max_attempts
            terminal = not decision.retryable or exhausted
            stored.status = "dead_letter" if exhausted else ("failed" if terminal else "retry")
            stored.error = redact_text(f"{decision.reason}: {exc}", limit=1000)
            set_run_failure(
                run,
                status="dead_letter" if exhausted else ("failed" if terminal else "pending"),
                code=decision.reason,
                exc=exc,
                retryable=decision.retryable,
                message=stored.error,
            )
            if terminal:
                stored.processed_at = datetime.now(timezone.utc)
            else:
                run.next_retry_at = datetime.now(timezone.utc) + timedelta(
                    seconds=decision.delay_seconds
                )
            await db.commit()
            if terminal:
                observe_terminal_run(run)
                _capture_exception(exc)
            else:
                observe_retry(run, decision.reason)
            logger.warning(
                "integration_file_failed",
                run_id=str(run.id),
                integration_id=str(run.integration_id),
                terminal=terminal,
                error_code=decision.reason,
                error_class=exc.__class__.__name__,
            )
            if not terminal:
                raise Retry(defer=decision.delay_seconds) from exc


async def dispatch_pending_inbox(ctx: dict) -> None:
    """Recupera enqueue perdido e leases abandonadas sem depender do Redis."""
    from sqlalchemy import or_

    from app.core.queue import enqueue_job
    from app.models import IntegrationInbox
    from app.repositories.integration import IntegrationRepository

    async with async_session_factory() as db:
        repo = IntegrationRepository(db)
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=5)
        # Reabre leases abandonadas pela queda abrupta de um worker.
        from sqlalchemy import update
        await db.execute(
            update(IntegrationInbox)
            .where(
                IntegrationInbox.status == "processing",
                or_(
                    IntegrationInbox.locked_at.is_(None),
                    IntegrationInbox.locked_at < stale_before,
                ),
            )
            .values(status="retry", available_at=datetime.now(timezone.utc), locked_at=None)
        )
        items = await repo.claim_due_inbox(limit=100)
        ids = [item.id for item in items]
        QUEUE_DEPTH.labels("inbox", "due").set(len(ids))
        await db.commit()
    for item_id in ids:
        await enqueue_job("process_inbox_job", inbox_id=str(item_id))


async def dispatch_pending_files(ctx: dict) -> None:
    """Recupera uploads cujo enqueue falhou e jobs interrompidos."""
    from sqlalchemy import select, update

    from app.core.queue import enqueue_job
    from app.models import IntegrationImportFile

    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=10)
    async with async_session_factory() as db:
        await db.execute(
            update(IntegrationImportFile)
            .where(
                IntegrationImportFile.status == "processing",
                IntegrationImportFile.updated_at < stale_before,
            )
            .values(status="retry")
        )
        result = await db.execute(
            select(IntegrationImportFile.run_id)
            .where(IntegrationImportFile.status.in_(("pending", "retry")))
            .order_by(IntegrationImportFile.created_at)
            .limit(50)
        )
        run_ids = list(result.scalars().all())
        QUEUE_DEPTH.labels("files", "due").set(len(run_ids))
        await db.commit()
    for run_id in run_ids:
        await enqueue_job("process_stock_file_job", run_id=str(run_id))


async def dispatch_pending_runs(ctx: dict) -> None:
    """Recupera runs API perdidos entre o commit e o enqueue."""
    from sqlalchemy import exists, or_, select, update

    from app.core.queue import enqueue_job
    from app.models import ERPIntegration, IntegrationImportFile, SyncExecution

    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=10)
    async with async_session_factory() as db:
        await db.execute(
            update(SyncExecution)
            .where(
                SyncExecution.status == "running",
                SyncExecution.updated_at < stale_before,
            )
            .values(
                status="pending",
                next_retry_at=now,
                message="Lease expirada; execução recuperada pelo dispatcher.",
            )
        )
        has_file = exists(
            select(IntegrationImportFile.id).where(
                IntegrationImportFile.run_id == SyncExecution.id
            )
        )
        result = await db.execute(
            select(
                SyncExecution.id,
                SyncExecution.integration_id,
                SyncExecution.entity,
            )
            .join(
                ERPIntegration,
                ERPIntegration.id == SyncExecution.integration_id,
            )
            .where(
                SyncExecution.status == "pending",
                SyncExecution.updated_at < now - timedelta(minutes=2),
                or_(
                    SyncExecution.next_retry_at.is_(None),
                    SyncExecution.next_retry_at <= now,
                ),
                ERPIntegration.type == "api",
                ERPIntegration.is_active.is_(True),
                ~has_file,
            )
            .order_by(SyncExecution.created_at)
            .limit(50)
        )
        runs = list(result.all())
        QUEUE_DEPTH.labels("runs", "due").set(len(runs))
        await db.commit()
    for run_id, integration_id, entity in runs:
        await enqueue_job(
            "run_sync_job",
            integration_id=str(integration_id),
            entity=entity,
            run_id=str(run_id),
        )


async def refresh_integration_alerts_job(ctx: dict) -> None:
    """Reavalia sucesso atrasado/falha recorrente e atualiza gauges."""
    from sqlalchemy import func, select

    from app.models import IntegrationAlert
    from app.services.integration_dashboard import refresh_all_integration_alerts

    async with async_session_factory() as db:
        await refresh_all_integration_alerts(db)
        rows = (
            await db.execute(
                select(
                    IntegrationAlert.kind,
                    IntegrationAlert.severity,
                    func.count(IntegrationAlert.id),
                )
                .where(IntegrationAlert.status == "open")
                .group_by(IntegrationAlert.kind, IntegrationAlert.severity)
            )
        ).all()
        await db.commit()
    for kind, severity, count in rows:
        OPEN_ALERTS.labels(kind, severity).set(count)
    logger.info("integration_alerts_refreshed", open_alerts=sum(r[2] for r in rows))


async def enforce_integration_retention_job(ctx: dict) -> None:
    """Expurga payloads/eventos vencidos mantendo diagnósticos agregados."""
    from app.services.integration_retention import enforce_integration_retention

    async with async_session_factory() as db:
        counts = await enforce_integration_retention(db)
        await db.commit()
    logger.info("integration_retention_completed", **counts)

async def send_notification_job(
    ctx: dict,
    *,
    tenant_id: str,
    user_id: str | None,
    customer_id: str | None,
    ntype: str,
    title: str,
    body: str | None = None,
) -> None:
    """Cria notificações em background (eventos de pedido/ticket/chat).

    notify_user/notify_customer exigem db (AsyncSession) — abrimos aqui.
    """
    from app.models.enums import NotificationType
    from app.services.notification import notify_customer, notify_user

    async with async_session_factory() as db:
        try:
            if user_id:
                await notify_user(
                    db, UUID(tenant_id), UUID(user_id),
                    NotificationType(ntype), title, body,
                )
            if customer_id:
                await notify_customer(
                    db, UUID(tenant_id), UUID(customer_id),
                    NotificationType(ntype), title, body,
                )
            await db.commit()
        finally:
            await db.close()
