"""Métricas Prometheus de integrações, compartilhadas por API e worker."""

from prometheus_client import Counter, Gauge, Histogram

RUNS_TOTAL = Counter(
    "integration_runs_total",
    "Execuções de integração concluídas.",
    ("trigger", "entity", "status"),
)
ITEMS_TOTAL = Counter(
    "integration_items_total",
    "Itens observados por resultado.",
    ("entity", "outcome"),
)
RUN_DURATION = Histogram(
    "integration_run_duration_seconds",
    "Duração das execuções de integração.",
    ("trigger", "entity", "status"),
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
RETRIES_TOTAL = Counter(
    "integration_retries_total",
    "Retries classificados de integração.",
    ("trigger", "reason"),
)
PAYLOADS_PURGED_TOTAL = Counter(
    "integration_payloads_purged_total",
    "Payloads removidos por retenção.",
    ("kind",),
)
QUEUE_DEPTH = Gauge(
    "integration_queue_depth",
    "Itens persistidos aguardando processamento.",
    ("queue", "status"),
)
OPEN_ALERTS = Gauge(
    "integration_open_alerts",
    "Alertas operacionais abertos.",
    ("kind", "severity"),
)
LAST_SUCCESS = Gauge(
    "integration_last_success_timestamp_seconds",
    "Timestamp do último sucesso por integração/capability.",
    ("integration_id", "entity"),
)


def observe_terminal_run(run) -> None:  # noqa: ANN001
    status = getattr(run.status, "value", run.status)
    trigger = run.trigger or "unknown"
    entity = run.entity or "unknown"
    RUNS_TOTAL.labels(trigger, entity, status).inc()
    if run.duration_ms is not None:
        RUN_DURATION.labels(trigger, entity, status).observe(run.duration_ms / 1000)
    counts = {
        "processed": run.processed,
        "created": run.created_count,
        "updated": run.updated_count,
        "unchanged": run.unchanged_count,
        "stale": run.stale_count,
        "skipped": run.skipped_count,
        "errors": run.errors,
    }
    for outcome, count in counts.items():
        if count:
            ITEMS_TOTAL.labels(entity, outcome).inc(count)
    if status in {"success", "partial"} and run.terminal_at:
        LAST_SUCCESS.labels(str(run.integration_id), entity).set(
            run.terminal_at.timestamp()
        )


def observe_retry(run, reason: str) -> None:  # noqa: ANN001
    RETRIES_TOTAL.labels(run.trigger or "unknown", reason[:80]).inc()
