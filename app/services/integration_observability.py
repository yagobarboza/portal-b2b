"""Normalização do diagnóstico persistido de um integration run."""

from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.redaction import redact_item_errors, redact_text


def finish_run_from_result(run, result: dict, *, items_received: int | None = None) -> None:  # noqa: ANN001
    settings = get_settings()
    details, truncated = redact_item_errors(
        result.get("details"), limit=settings.INTEGRATION_MAX_ITEM_ERRORS
    )
    run.items_received = items_received if items_received is not None else (
        int(result.get("processed", 0))
        + int(result.get("unchanged", 0))
        + int(result.get("stale", 0))
        + int(result.get("errors", 0))
    )
    run.created_count = int(result.get("created", 0))
    run.updated_count = int(result.get("updated", result.get("processed", 0)))
    run.unchanged_count = int(result.get("unchanged", 0))
    run.stale_count = int(result.get("stale", 0))
    run.skipped_count = int(result.get("skipped", 0))
    run.item_errors = details or None
    run.item_errors_truncated = truncated
    run.message = redact_text(result.get("message") or "", limit=500) or None
    if run.started_at and run.finished_at:
        started = run.started_at
        finished = run.finished_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        run.duration_ms = max(0, int((finished - started).total_seconds() * 1000))


def set_run_failure(
    run,
    *,
    status: str,
    code: str,
    exc: Exception | None = None,
    retryable: bool | None = None,
    message: str | None = None,
) -> None:  # noqa: ANN001
    now = datetime.now(timezone.utc)
    run.status = status
    run.error_code = code[:80]
    run.error_class = exc.__class__.__name__[:160] if exc else None
    run.retryable = retryable
    run.message = redact_text(message or (str(exc) if exc else code), limit=500)
    if status in {"failed", "dead_letter"}:
        run.finished_at = now
        run.terminal_at = now
        if run.started_at:
            started = run.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            run.duration_ms = max(0, int((now - started).total_seconds() * 1000))
