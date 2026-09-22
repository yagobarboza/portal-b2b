"""Regressões críticas da FASE 2, sem dependência de PostgreSQL/Redis reais."""

import asyncio
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.endpoints import webhooks as webhook_endpoint
from app.core.crypto import decrypt_str, encrypt_str
from app.core.network_security import UnsafeOutboundUrlError, validate_public_https_url
from app.core.permissions import (
    INTEGRATION_MANAGE,
    INTEGRATION_READ,
    INTEGRATION_RUN,
    INTEGRATION_SECRETS,
)
from app.models.enums import WebhookStatus
from app.schemas.integration import ApiPullConfigIn
from app.services.api_pull import build_stored_config
from app.services.integration import (
    process_webhook_payload,
    run_sync,
    sign_webhook,
    upsert_financial_accounts,
    verify_webhook_signature,
)
from app.services.rbac import (
    ROLE_ADMIN,
    ROLE_DEFINITIONS,
    ROLE_SUPORTE_TECNICO,
)


def _integration(secret: str, *, previous: str | None = None):
    integration = SimpleNamespace(id=uuid4())
    integration.credential = SimpleNamespace(
        payload={"secret": encrypt_str(secret)},
        previous_payload=(
            {"secret": encrypt_str(previous)} if previous else None
        ),
        rotated_at=datetime.now(timezone.utc),
    )
    return integration


@pytest.mark.asyncio
async def test_webhook_signature_binds_timestamp_and_integration() -> None:
    integration = _integration("current-secret")
    body = b'{"event":"stock.sync","records":[]}'
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signature = sign_webhook(body, "current-secret", integration.id, timestamp)

    assert await verify_webhook_signature(
        body, signature, timestamp, integration, integration.credential
    )
    assert not await verify_webhook_signature(
        body, signature, timestamp, SimpleNamespace(**{
            **integration.__dict__, "id": uuid4(),
        }), integration.credential
    )


@pytest.mark.asyncio
async def test_webhook_rejects_expired_timestamp_and_accepts_previous_secret() -> None:
    integration = _integration("new-secret", previous="old-secret")
    body = b'{"event":"stock.sync","records":[]}'
    current = str(int(datetime.now(timezone.utc).timestamp()))
    previous_signature = sign_webhook(body, "old-secret", integration.id, current)
    assert await verify_webhook_signature(
        body, previous_signature, current, integration, integration.credential
    )

    expired = str(int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()))
    expired_signature = sign_webhook(body, "new-secret", integration.id, expired)
    assert not await verify_webhook_signature(
        body, expired_signature, expired, integration, integration.credential
    )


def test_api_config_preserves_existing_secret_explicitly() -> None:
    encrypted = encrypt_str("token-original")
    body = ApiPullConfigIn(
        base_url="https://erp.example.com",
        auth_type="bearer",
        token_mode="keep",
        headers_mode="keep",
    )
    stored = build_stored_config(
        body,
        {"token": encrypted, "headers": {"X-ERP-Key": encrypt_str("value")}},
    )
    assert stored["token"] == encrypted
    assert decrypt_str(stored["headers"]["X-ERP-Key"]) == "value"


def test_api_config_rejects_plain_http() -> None:
    with pytest.raises(ValidationError):
        ApiPullConfigIn(base_url="http://erp.example.com")


@pytest.mark.asyncio
async def test_ssrf_rejects_private_and_loopback_targets(monkeypatch) -> None:
    with pytest.raises(UnsafeOutboundUrlError):
        await validate_public_https_url("https://127.0.0.1/stock")
    with pytest.raises(UnsafeOutboundUrlError):
        await validate_public_https_url("https://169.254.169.254/latest/meta-data")
    with pytest.raises(UnsafeOutboundUrlError):
        await validate_public_https_url("https://100.64.0.1/stock")

    loop = asyncio.get_running_loop()

    async def private_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", private_dns)
    with pytest.raises(UnsafeOutboundUrlError):
        await validate_public_https_url("https://erp.example.com/stock")


@pytest.mark.asyncio
async def test_financial_sync_rejects_customer_outside_tenant() -> None:
    class EmptyResult:
        def scalar_one_or_none(self):
            return None

    class FakeDb:
        async def execute(self, statement):
            return EmptyResult()

        async def flush(self):
            return None

    processed, errors, message = await upsert_financial_accounts(
        FakeDb(),
        uuid4(),
        [{
            "external_id": "invoice-1",
            "customer_id": str(uuid4()),
            "value": "10.00",
        }],
    )
    assert processed == 0
    assert errors == 1
    assert "tenant" in message.lower()


@pytest.mark.asyncio
async def test_generic_sync_never_falls_back_to_demo_records() -> None:
    integration = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    with pytest.raises(RuntimeError, match="connector"):
        await run_sync(None, integration, "product")


@pytest.mark.asyncio
async def test_webhook_processing_failure_propagates_from_service() -> None:
    event = SimpleNamespace(status="received", processed_at=None, error=None)
    with pytest.raises(ValueError, match="não suportado"):
        await process_webhook_payload(
            None,
            SimpleNamespace(id=uuid4(), tenant_id=uuid4()),
            event,
            {"event": "unsupported", "records": []},
        )
    assert event.status == "received"


@pytest.mark.asyncio
async def test_webhook_endpoint_persists_before_processing(monkeypatch) -> None:
    integration = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), is_active=True, type="webhook"
    )
    event = SimpleNamespace(
        id=uuid4(),
        payload={"event": "stock.sync", "records": []},
        status="received",
        received_at=datetime.now(timezone.utc),
        processed_at=None,
        error=None,
    )
    operations: list[str] = []
    inbox = SimpleNamespace(id=uuid4(), run_id=None)

    class FakeRequest:
        headers = {
            "x-webhook-signature": "valid",
            "x-webhook-timestamp": "1",
            "x-idempotency-key": "event-1",
        }

        async def body(self):
            return b'{"event":"stock.sync","records":[]}'

    class FakeDb:
        async def commit(self):
            operations.append("commit")

        async def rollback(self):
            operations.append("rollback")

    class FakeRepo:
        def __init__(self, db):
            pass

        async def get(self, integration_id):
            return integration

        async def get_webhook_credential(self, integration):
            return None

        async def get_webhook_event_by_idempotency(self, *args, **kwargs):
            return None

        async def create_or_get_inbox(self, **kwargs):
            operations.append("inbox")
            return inbox, True

        async def create_sync(self, *args, **kwargs):
            operations.append("run")
            return SimpleNamespace(id=uuid4())

        async def create_webhook_event(self, *args, **kwargs):
            operations.append("create")
            return event

        async def get_webhook_event(self, event_id):
            return event

    async def always_true(*args, **kwargs):
        return True

    async def enqueue(*args, **kwargs):
        operations.append("enqueue")
        return True

    monkeypatch.setattr(webhook_endpoint, "IntegrationRepository", FakeRepo)
    monkeypatch.setattr(webhook_endpoint, "verify_webhook_signature", always_true)
    monkeypatch.setattr(webhook_endpoint, "webhook_rate_limit", always_true)
    monkeypatch.setattr(webhook_endpoint, "enqueue_job", enqueue)

    result = await webhook_endpoint.receive_webhook(
        integration.id, FakeRequest(), FakeDb()
    )
    assert result.status == "accepted"
    assert operations.index("inbox") < operations.index("run") < operations.index("create")
    assert operations.index("create") < operations.index("commit") < operations.index("enqueue")


@pytest.mark.asyncio
async def test_webhook_endpoint_acks_when_queue_is_temporarily_down(monkeypatch) -> None:
    integration = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), is_active=True, type="webhook"
    )
    event = SimpleNamespace(
        id=uuid4(),
        payload={"event": "stock.sync", "records": []},
        status="received",
        received_at=datetime.now(timezone.utc),
        processed_at=None,
        error=None,
    )
    inbox = SimpleNamespace(id=uuid4(), run_id=None)

    class FakeRequest:
        headers = {
            "x-webhook-signature": "valid",
            "x-webhook-timestamp": "1",
            "x-idempotency-key": "event-2",
        }

        async def body(self):
            return b'{"event":"stock.sync","records":[]}'

    class FakeDb:
        async def commit(self):
            return None

        async def rollback(self):
            return None

    class FakeRepo:
        def __init__(self, db):
            pass

        async def get(self, integration_id):
            return integration

        async def get_webhook_credential(self, integration):
            return None

        async def get_webhook_event_by_idempotency(self, *args, **kwargs):
            return None

        async def create_or_get_inbox(self, **kwargs):
            return inbox, True

        async def create_sync(self, *args, **kwargs):
            return SimpleNamespace(id=uuid4())

        async def create_webhook_event(self, *args, **kwargs):
            return event

        async def get_webhook_event(self, event_id):
            return event

    async def always_true(*args, **kwargs):
        return True

    async def queue_down(*args, **kwargs):
        return False

    monkeypatch.setattr(webhook_endpoint, "IntegrationRepository", FakeRepo)
    monkeypatch.setattr(webhook_endpoint, "verify_webhook_signature", always_true)
    monkeypatch.setattr(webhook_endpoint, "webhook_rate_limit", always_true)
    monkeypatch.setattr(webhook_endpoint, "enqueue_job", queue_down)

    result = await webhook_endpoint.receive_webhook(
        integration.id, FakeRequest(), FakeDb()
    )
    assert result.status == "accepted"
    assert event.status == "received"


@pytest.mark.asyncio
async def test_webhook_endpoint_returns_idempotent_ack_for_duplicate(monkeypatch) -> None:
    integration = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), is_active=True, type="webhook"
    )
    event = SimpleNamespace(
        id=uuid4(),
        payload={"event": "stock.sync", "records": []},
        status=WebhookStatus.RECEIVED,
        received_at=datetime.now(timezone.utc),
        processed_at=None,
        error=None,
    )
    inbox = SimpleNamespace(id=uuid4())

    class FakeRequest:
        headers = {
            "x-webhook-signature": "valid",
            "x-webhook-timestamp": "1",
            "x-idempotency-key": "event-abandoned",
        }

        async def body(self):
            return b'{"event":"stock.sync","records":[]}'

    class FakeDb:
        async def commit(self):
            return None

        async def rollback(self):
            return None

    class FakeRepo:
        def __init__(self, db):
            pass

        async def get(self, integration_id):
            return integration

        async def get_webhook_credential(self, integration):
            return None

        async def get_webhook_event_by_idempotency(self, *args, **kwargs):
            return event

        async def create_or_get_inbox(self, **kwargs):
            return inbox, False

        async def get_webhook_event(self, event_id):
            return event

    async def always_true(*args, **kwargs):
        return True

    monkeypatch.setattr(webhook_endpoint, "IntegrationRepository", FakeRepo)
    monkeypatch.setattr(webhook_endpoint, "verify_webhook_signature", always_true)
    monkeypatch.setattr(webhook_endpoint, "webhook_rate_limit", always_true)

    result = await webhook_endpoint.receive_webhook(
        integration.id, FakeRequest(), FakeDb()
    )
    assert result.status == "duplicate"
    assert result.event_id == event.id


def test_admin_role_contains_all_integration_permissions() -> None:
    expected = {
        INTEGRATION_READ,
        INTEGRATION_MANAGE,
        INTEGRATION_RUN,
        INTEGRATION_SECRETS,
    }
    assert expected.issubset(set(ROLE_DEFINITIONS[ROLE_ADMIN]["permissions"]))


def test_technical_support_is_native_integration_role() -> None:
    expected = {
        INTEGRATION_READ,
        INTEGRATION_MANAGE,
        INTEGRATION_RUN,
        INTEGRATION_SECRETS,
    }
    definition = ROLE_DEFINITIONS[ROLE_SUPORTE_TECNICO]

    assert definition["name"] == "Suporte Técnico"
    assert definition["is_system"] is True
    assert definition["global"] is False
    assert set(definition["permissions"]) == expected
