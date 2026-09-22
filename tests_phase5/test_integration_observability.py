from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.monitoring import _before_breadcrumb, _before_send
from app.core.redaction import REDACTED, redact_item_errors, redact_text, redact_value
from app.services.integration_observability import finish_run_from_result, set_run_failure


def _run():
    started = datetime.now(timezone.utc) - timedelta(seconds=2)
    return SimpleNamespace(
        status="running",
        started_at=started,
        finished_at=datetime.now(timezone.utc),
        terminal_at=None,
        items_received=0,
        created_count=0,
        updated_count=0,
        unchanged_count=0,
        stale_count=0,
        skipped_count=0,
        item_errors=None,
        item_errors_truncated=0,
        duration_ms=None,
        error_code=None,
        error_class=None,
        retryable=None,
        message=None,
    )


def test_recursive_redaction_removes_secrets_and_pii():
    safe = redact_value(
        {
            "authorization": "Bearer abc.def.ghi",
            "nested": {"password": "secret", "message": "user@example.com"},
            "url": "https://erp.test/items?token=very-secret&cursor=1",
        }
    )
    assert safe["authorization"] == REDACTED
    assert safe["nested"]["password"] == REDACTED
    assert "user@example.com" not in safe["nested"]["message"]
    assert "very-secret" not in safe["url"]


def test_item_errors_are_allowlisted_limited_and_redacted():
    details = [
        {"row": 2, "sku": "ABC", "error": "cliente user@example.com", "raw": {"token": "x"}},
        {"row": 3, "sku": "DEF", "error": "inválido"},
    ]
    safe, truncated = redact_item_errors(details, limit=1)
    assert safe == [{"row": 2, "sku": "ABC", "error": "cliente [REDACTED]"}]
    assert truncated == 1


def test_finish_run_persists_bounded_diagnostics_and_counters():
    run = _run()
    finish_run_from_result(
        run,
        {
            "processed": 4,
            "created": 1,
            "updated": 3,
            "unchanged": 2,
            "stale": 1,
            "errors": 1,
            "message": "erro para user@example.com",
            "details": [{"sku": "A", "error": "token=abc"}],
        },
        items_received=8,
    )
    assert run.items_received == 8
    assert (run.created_count, run.updated_count, run.unchanged_count, run.stale_count) == (1, 3, 2, 1)
    assert run.duration_ms >= 1900
    assert "user@example.com" not in run.message
    assert set(run.item_errors[0]) <= {"row", "index", "sku", "error", "code"}


def test_failure_classification_never_persists_raw_pii():
    run = _run()
    exc = RuntimeError("Bearer super-secret user@example.com")
    set_run_failure(run, status="dead_letter", code="upstream_401", exc=exc, retryable=False)
    assert run.status == "dead_letter"
    assert run.error_class == "RuntimeError"
    assert run.error_code == "upstream_401"
    assert run.terminal_at is not None
    assert "super-secret" not in run.message
    assert "user@example.com" not in run.message


def test_sentry_hooks_strip_request_body_user_and_breadcrumb_secrets():
    event = _before_send(
        {
            "request": {"data": {"sku": "A", "token": "secret"}},
            "user": {"email": "user@example.com"},
            "extra": {"api_key": "secret"},
        },
        {},
    )
    assert event["request"]["data"] == REDACTED
    assert "user" not in event
    assert event["extra"]["api_key"] == REDACTED
    crumb = _before_breadcrumb({"message": "Bearer abcdefghij"}, {})
    assert "abcdefghij" not in crumb["message"]


def test_redact_text_is_bounded():
    assert len(redact_text("x" * 1000, limit=80)) == 80
