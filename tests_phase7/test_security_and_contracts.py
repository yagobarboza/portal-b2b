from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.deps import require_permission
from app.core.exceptions import ForbiddenError
from app.core.network_security import UnsafeOutboundUrlError, validate_public_https_url
from app.core.permissions import INTEGRATION_MANAGE
from app.integrations import connectors  # noqa: F401 - registra implementações
from app.integrations.contracts import NormalizedProduct, StockUpdate
from app.integrations.interfaces import Capability, ProductAdapter, StockAdapter
from app.integrations.registry import integration_registry
from app.integrations.retry import classify_retry
from app.schemas.integration import ApiPullConfigIn
from app.services.api_pull import build_stored_config
from app.services.integration import sign_webhook, verify_webhook_signature


def _http_error(status: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://erp.example/stock")
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = httpx.Response(status, request=request, headers=headers)
    return httpx.HTTPStatusError("ERP failure", request=request, response=response)


@pytest.mark.parametrize(
    ("error", "reason", "delay"),
    [
        (httpx.ConnectError("indisponível"), "transient_io", None),
        (httpx.ReadTimeout("timeout"), "transient_io", None),
        (_http_error(429, "41"), "http_429", 41),
        (_http_error(500), "http_500", None),
    ],
)
def test_erp_failures_are_retryable(error, reason, delay) -> None:
    decision = classify_retry(error, attempt=1)
    assert decision.retryable is True
    assert decision.reason == reason
    if delay is not None:
        assert decision.delay_seconds == delay


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://erp.example.com/stock",
        "https://127.0.0.1/stock",
        "https://10.10.0.1/stock",
        "https://172.16.0.1/stock",
        "https://192.168.1.1/stock",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/stock",
    ],
)
async def test_ssrf_blocks_insecure_loopback_private_and_link_local(url) -> None:
    with pytest.raises(UnsafeOutboundUrlError):
        await validate_public_https_url(url)


@pytest.mark.asyncio
async def test_rest_connector_refuses_redirect(monkeypatch) -> None:
    from app.integrations.connectors import rest_json

    async def allow_test_host(url: str) -> None:
        return None

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, request=request, headers={"Location": "http://127.0.0.1/admin"}
        )
    )
    monkeypatch.setattr(rest_json, "validate_public_https_url", allow_test_host)
    monkeypatch.setattr(
        rest_json.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=transport, follow_redirects=False),
    )
    with pytest.raises(httpx.HTTPStatusError, match="Redirects não são permitidos"):
        await rest_json.fetch_json(
            {"base_url": "https://erp.example", "path": "/stock"}
        )


@pytest.mark.asyncio
async def test_expired_previous_webhook_credential_is_rejected(monkeypatch) -> None:
    from app.services import integration as service

    monkeypatch.setattr(service, "decrypt_str", lambda value: value)
    integration = SimpleNamespace(id=uuid4())
    credential = SimpleNamespace(
        payload={"secret": "current"},
        previous_payload={"secret": "expired"},
        rotated_at=datetime.now(timezone.utc)
        - timedelta(
            seconds=service.settings.WEBHOOK_SECRET_ROTATION_GRACE_SECONDS + 1
        ),
    )
    body = b'{"event":"stock.sync","records":[]}'
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signature = sign_webhook(body, "expired", integration.id, timestamp)
    assert not await verify_webhook_signature(
        body, signature, timestamp, integration, credential
    )


@pytest.mark.asyncio
async def test_user_without_permission_cannot_manage_integrations() -> None:
    checker = require_permission(INTEGRATION_MANAGE)
    user = SimpleNamespace(is_super_admin=False, roles=[])
    with pytest.raises(ForbiddenError):
        await checker(user)


def test_secret_modes_preserve_replace_and_clear(monkeypatch) -> None:
    monkeypatch.setattr("app.services.api_pull.encrypt_str", lambda value: f"enc:{value}")
    existing = {
        "base_url": "https://old.example",
        "token": "enc:old",
        "headers": {"X-Key": "enc:header"},
    }
    kept = build_stored_config(
        ApiPullConfigIn(
            base_url="https://erp.example",
            auth_type="bearer",
            token_mode="keep",
            headers_mode="keep",
        ),
        existing,
    )
    assert kept["token"] == "enc:old"
    assert kept["headers"] == existing["headers"]

    replaced = build_stored_config(
        ApiPullConfigIn(
            base_url="https://erp.example",
            auth_type="bearer",
            token="new",
            token_mode="replace",
            headers={"X-New": "value"},
            headers_mode="replace",
        ),
        existing,
    )
    assert replaced["token"] == "enc:new"
    assert replaced["headers"] == {"X-New": "enc:value"}

    cleared = build_stored_config(
        ApiPullConfigIn(
            base_url="https://erp.example",
            auth_type="none",
            token_mode="clear",
            headers_mode="clear",
        ),
        existing,
    )
    assert "token" not in cleared
    assert cleared["headers"] == {}


@pytest.mark.parametrize(
    ("capability", "record", "contract", "protocol"),
    [
        (
            Capability.STOCK,
            {"sku": " adapter ", "stock": "5"},
            StockUpdate,
            StockAdapter,
        ),
        (
            Capability.PRODUCTS,
            {"sku": " adapter ", "name": "Produto"},
            NormalizedProduct,
            ProductAdapter,
        ),
    ],
)
def test_every_registered_adapter_honors_its_canonical_contract(
    capability, record, contract, protocol
) -> None:
    adapter = integration_registry.adapter("rest_json", capability, config={})
    assert isinstance(adapter, protocol)
    result = (
        adapter.adapt_stock(record)
        if capability == Capability.STOCK
        else adapter.adapt_product(record)
    )
    assert isinstance(result, contract)
    assert result.sku == "ADAPTER"
