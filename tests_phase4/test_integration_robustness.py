"""Regressões mínimas da FASE 4: concorrência, retry e versionamento."""

from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.connectors import rest_json
from app.integrations.retry import (
    classify_retry,
    exponential_backoff,
    parse_retry_after,
)
from app.models import IntegrationInbox, IntegrationSchedule, Product
from app.models.enums import SyncStatus, WebhookStatus
from app.schemas.integration import ApiPullConfigIn, StockSyncResult
from app.services.api_pull import build_stored_config, masked_config
from app.services.stock_sync import parse_stock_records, parse_stock_rows


def _http_error(status: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://erp.example/stock")
    response = httpx.Response(
        status, request=request, headers={"Retry-After": retry_after} if retry_after else {}
    )
    return httpx.HTTPStatusError("failure", request=request, response=response)


def test_retry_after_seconds_and_http_date_are_supported() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    assert parse_retry_after("120", now=now) == 120
    assert parse_retry_after("Mon, 21 Sep 2026 12:02:00 GMT", now=now) == 120


def test_http_429_honors_retry_after() -> None:
    decision = classify_retry(_http_error(429, "37"), attempt=1)
    assert decision.retryable is True
    assert decision.delay_seconds == 37
    assert decision.reason == "http_429"


def test_http_400_is_terminal_but_503_is_retryable() -> None:
    assert classify_retry(_http_error(400), attempt=1).retryable is False
    assert classify_retry(_http_error(503), attempt=1).retryable is True


def test_backoff_is_capped_and_uses_jitter() -> None:
    assert exponential_backoff(20, cap=900, rng=lambda low, high: high) == 900


def test_phase4_columns_and_unique_inbox_constraint_are_mapped() -> None:
    assert {"stock_updated_at", "stock_source_version"} <= set(Product.__table__.c.keys())
    constraint_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in IntegrationInbox.__table__.constraints
        if hasattr(constraint, "columns")
    }
    assert ("integration_id", "channel", "idempotency_key") in constraint_columns
    assert {"cursor", "jitter_seconds", "max_batch_size"} <= set(
        IntegrationSchedule.__table__.c.keys()
    )


def test_terminal_states_are_public_contract_values() -> None:
    assert SyncStatus.DEAD_LETTER.value == "dead_letter"
    assert WebhookStatus.PROCESSING.value == "processing"
    assert WebhookStatus.DEAD_LETTER.value == "dead_letter"


def test_result_is_backward_compatible_with_stale_default() -> None:
    result = StockSyncResult(
        sync_id="00000000-0000-0000-0000-000000000001",
        status="ok",
        processed=1,
        unchanged=0,
        errors=0,
    )
    assert result.stale == 0


def test_file_and_webhook_keep_stock_clock_and_version() -> None:
    occurred_at = "2026-09-21T12:00:00Z"
    file_items, file_errors = parse_stock_rows(
        [
            {
                "sku": "A",
                "stock": "2",
                "occurred_at": occurred_at,
                "source_version": "42",
            }
        ]
    )
    webhook_items, webhook_errors = parse_stock_records(
        [
            {
                "sku": "A",
                "stock": 2,
                "occurred_at": occurred_at,
                "source_version": "42",
            }
        ]
    )
    assert not file_errors and not webhook_errors
    assert file_items[0].source_version == "42"
    assert webhook_items[0].source_version == "42"
    assert file_items[0].occurred_at == webhook_items[0].occurred_at


def test_cursor_and_stock_version_fields_survive_config_roundtrip() -> None:
    body = ApiPullConfigIn(
        base_url="https://erp.example",
        occurred_at_field="metadata.changed_at",
        source_version_field="metadata.version",
        cursor_param="after",
        next_cursor_path="pagination.next",
    )
    stored = build_stored_config(body)
    visible = masked_config(stored)
    assert visible["occurred_at_field"] == "metadata.changed_at"
    assert visible["source_version_field"] == "metadata.version"
    assert visible["cursor_param"] == "after"
    assert visible["next_cursor_path"] == "pagination.next"


@pytest.mark.asyncio
async def test_rest_connector_exposes_next_cursor(monkeypatch) -> None:
    async def fake_fetch(config):
        return 200, {"items": [{"sku": "A", "stock": 1}], "next": "page-2"}

    monkeypatch.setattr(rest_json, "fetch_json", fake_fetch)
    connector = rest_json.RestJsonStockConnector(
        config={"data_path": "items", "next_cursor_path": "next"}
    )
    assert [item async for item in connector.fetch_stock()] == [
        {"sku": "A", "stock": 1}
    ]
    assert connector.next_cursor == "page-2"
