"""Alertas persistidos e visão operacional tenant-scoped de integrações."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import ERPIntegration, IntegrationAlert, SyncExecution

SUCCESS_STATUSES = {"success", "partial"}
FAILURE_STATUSES = {"failed", "dead_letter"}
TERMINAL_STATUSES = SUCCESS_STATUSES | FAILURE_STATUSES


def _status(value) -> str:  # noqa: ANN001
    return str(getattr(value, "value", value))


async def _set_alert(
    db: AsyncSession,
    *,
    integration: ERPIntegration,
    kind: str,
    active: bool,
    severity: str,
    message: str,
    consecutive_failures: int = 0,
    last_run_id=None,
) -> None:
    now = datetime.now(timezone.utc)
    if active:
        statement = pg_insert(IntegrationAlert).values(
            tenant_id=integration.tenant_id,
            integration_id=integration.id,
            kind=kind,
            status="open",
            severity=severity,
            message=message[:500],
            consecutive_failures=consecutive_failures,
            opened_at=now,
            last_seen_at=now,
            resolved_at=None,
            last_run_id=last_run_id,
        )
        await db.execute(
            statement.on_conflict_do_update(
                constraint="uq_integration_alerts_kind",
                set_={
                    "status": "open",
                    "severity": severity,
                    "message": message[:500],
                    "consecutive_failures": consecutive_failures,
                    "opened_at": case(
                        (IntegrationAlert.status != "open", now),
                        else_=IntegrationAlert.opened_at,
                    ),
                    "last_seen_at": now,
                    "resolved_at": None,
                    "last_run_id": last_run_id,
                    "updated_at": now,
                },
            )
        )
    else:
        await db.execute(
            update(IntegrationAlert)
            .where(
                IntegrationAlert.integration_id == integration.id,
                IntegrationAlert.kind == kind,
                IntegrationAlert.status == "open",
            )
            .values(
                status="resolved",
                resolved_at=now,
                last_seen_at=now,
                consecutive_failures=0,
                updated_at=now,
            )
        )


async def evaluate_integration_alerts(
    db: AsyncSession, integration: ERPIntegration
) -> None:
    settings = get_settings()
    result = await db.execute(
        select(SyncExecution)
        .where(
            SyncExecution.integration_id == integration.id,
            SyncExecution.status.in_(tuple(TERMINAL_STATUSES)),
        )
        .order_by(SyncExecution.created_at.desc())
        .limit(50)
    )
    runs = list(result.scalars().all())
    consecutive = 0
    for run in runs:
        if _status(run.status) in FAILURE_STATUSES:
            consecutive += 1
        else:
            break
    threshold = max(1, settings.INTEGRATION_FAILURE_ALERT_THRESHOLD)
    last_run = runs[0] if runs else None
    await _set_alert(
        db,
        integration=integration,
        kind="recurring_failure",
        active=integration.is_active and consecutive >= threshold,
        severity="critical",
        message=f"Integração falhou {consecutive} vezes consecutivas.",
        consecutive_failures=consecutive,
        last_run_id=last_run.id if last_run else None,
    )

    last_success = next(
        (run for run in runs if _status(run.status) in SUCCESS_STATUSES), None
    )
    baseline = (
        last_success.terminal_at or last_success.finished_at
        if last_success
        else integration.created_at
    )
    if baseline.tzinfo is None:
        baseline = baseline.replace(tzinfo=timezone.utc)
    stale = datetime.now(timezone.utc) - baseline > timedelta(
        hours=max(1, settings.INTEGRATION_STALE_SUCCESS_HOURS)
    )
    await _set_alert(
        db,
        integration=integration,
        kind="stale_success",
        active=integration.is_active and stale,
        severity="warning",
        message=(
            f"Sem sincronização bem-sucedida há mais de "
            f"{settings.INTEGRATION_STALE_SUCCESS_HOURS} hora(s)."
        ),
        last_run_id=last_run.id if last_run else None,
    )


async def refresh_all_integration_alerts(db: AsyncSession) -> None:
    result = await db.execute(select(ERPIntegration))
    for integration in result.scalars().all():
        await evaluate_integration_alerts(db, integration)
    await db.flush()


async def build_integration_dashboard(
    db: AsyncSession, tenant_id
) -> dict:  # noqa: ANN001
    integrations = list(
        (
            await db.execute(
                select(ERPIntegration)
                .where(ERPIntegration.tenant_id == tenant_id)
                .order_by(ERPIntegration.name)
            )
        )
        .scalars()
        .all()
    )
    ids = [integration.id for integration in integrations]
    if not ids:
        return {"generated_at": datetime.now(timezone.utc), "integrations": [], "alerts": []}

    ranked = (
        select(
            SyncExecution.integration_id.label("integration_id"),
            SyncExecution.id.label("id"),
            SyncExecution.status.label("status"),
            SyncExecution.created_at.label("created_at"),
            SyncExecution.terminal_at.label("terminal_at"),
            SyncExecution.duration_ms.label("duration_ms"),
            func.row_number()
            .over(
                partition_by=SyncExecution.integration_id,
                order_by=SyncExecution.created_at.desc(),
            )
            .label("position"),
        )
        .where(SyncExecution.integration_id.in_(ids))
        .subquery()
    )
    recent_rows = (
        await db.execute(select(ranked).where(ranked.c.position <= 50))
    ).mappings().all()
    runs_by_integration: dict = {integration_id: [] for integration_id in ids}
    for row in recent_rows:
        runs_by_integration[row["integration_id"]].append(row)
    for runs in runs_by_integration.values():
        runs.sort(key=lambda row: row["created_at"], reverse=True)

    alerts = list(
        (
            await db.execute(
                select(IntegrationAlert)
                .where(
                    IntegrationAlert.tenant_id == tenant_id,
                    IntegrationAlert.status == "open",
                )
                .order_by(IntegrationAlert.severity, IntegrationAlert.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    alerts_by_integration: dict = {integration_id: [] for integration_id in ids}
    for alert in alerts:
        alerts_by_integration.setdefault(alert.integration_id, []).append(alert)

    items = []
    for integration in integrations:
        runs = runs_by_integration.get(integration.id, [])
        last = runs[0] if runs else None
        successes = [row for row in runs if _status(row["status"]) in SUCCESS_STATUSES]
        failures = [row for row in runs if _status(row["status"]) in FAILURE_STATUSES]
        consecutive = 0
        for row in runs:
            if _status(row["status"]) in FAILURE_STATUSES:
                consecutive += 1
            elif _status(row["status"]) in SUCCESS_STATUSES:
                break
        durations = [row["duration_ms"] for row in runs if row["duration_ms"] is not None]
        current_alerts = alerts_by_integration.get(integration.id, [])
        if any(alert.severity == "critical" for alert in current_alerts):
            health = "failing"
        elif current_alerts:
            health = "warning"
        elif not runs:
            health = "never_run"
        else:
            health = "healthy"
        items.append(
            {
                "integration_id": integration.id,
                "name": integration.name,
                "type": integration.type,
                "is_active": integration.is_active,
                "health": health,
                "last_status": _status(last["status"]) if last else None,
                "last_run_at": last["created_at"] if last else None,
                "last_success_at": (
                    successes[0]["terminal_at"] or successes[0]["created_at"]
                    if successes
                    else None
                ),
                "last_failure_at": (
                    failures[0]["terminal_at"] or failures[0]["created_at"]
                    if failures
                    else None
                ),
                "consecutive_failures": consecutive,
                "average_duration_ms": (
                    int(sum(durations) / len(durations)) if durations else None
                ),
                "pending_runs": sum(
                    1 for row in runs if _status(row["status"]) in {"pending", "running"}
                ),
                "open_alerts": len(current_alerts),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc),
        "integrations": items,
        "alerts": alerts,
    }
