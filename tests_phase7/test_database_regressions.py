from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.database.session import async_session_factory
from app.integrations.adapters import MappingStockAdapter
from app.integrations.contracts import NormalizedProduct, StockUpdate
from app.models import (
    ERPIntegration,
    IntegrationCredential,
    IntegrationInbox,
    Product,
    SyncExecution,
    WebhookEvent,
)
from app.models.enums import ProductStatus
from app.repositories.integration import IntegrationRepository
from app.services.api_pull import fetch_and_apply_stock
from app.services.integration import process_webhook_payload
from app.services.product_sync import ProductSyncService
from app.services.stock_sync import apply_stock_import, apply_stock_sync
from worker.jobs import process_inbox_job


async def _integration(db, fixture, channel: str) -> ERPIntegration:
    integration = await db.get(ERPIntegration, fixture.integration_ids[channel])
    assert integration is not None
    return integration


def _product(fixture, sku: str, *, stock: int | None = 1) -> Product:
    return Product(
        tenant_id=fixture.tenant_id,
        sku=sku,
        normalized_sku=sku.strip().upper(),
        name=f"Produto {sku}",
        price=Decimal("10.00"),
        stock=stock,
        status=ProductStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_product_upsert_updates_creates_deduplicates_and_isolates_tenants(
    integration_fixture,
) -> None:
    fixture = integration_fixture
    async with async_session_factory() as db:
        integration = await _integration(db, fixture, "api")
        existing = _product(fixture, "SKU-EXISTING")
        db.add(existing)
        await db.commit()

        batch = [
            NormalizedProduct(
                sku="sku-existing",
                external_id="erp-existing",
                name="Nome atualizado",
                price="20.50",
            ),
            NormalizedProduct(
                sku="sku-new",
                external_id="erp-new",
                name="Rascunho que deve perder",
            ),
            NormalizedProduct(
                sku=" SKU-NEW ",
                external_id="erp-new",
                name="Produto novo",
                stock=3,
            ),
        ]
        first = await ProductSyncService(db).sync(
            integration=integration, products=batch
        )
        await db.commit()
        assert (first["created"], first["updated"], first["errors"]) == (1, 1, 0)

        second = await ProductSyncService(db).sync(
            integration=integration, products=batch
        )
        await db.commit()
        assert second["created"] == 0
        assert second["unchanged"] == 2
        rows = (
            await db.execute(
                select(Product).where(Product.tenant_id == fixture.tenant_id)
            )
        ).scalars().all()
        assert len(rows) == 2
        by_sku = {row.normalized_sku: row for row in rows}
        assert by_sku["SKU-EXISTING"].name == "Nome atualizado"
        assert by_sku["SKU-NEW"].name == "Produto novo"

        # A mesma chave canônica é válida em outro tenant.
        other_fixture_suffix = fixture.tenant_id.hex[:12]
        from app.models import Company

        other = Company(
            name="Outro tenant Phase 7",
            cnpj=f"X{other_fixture_suffix}",
            slug=f"other-phase-7-{fixture.tenant_id.hex}",
        )
        db.add(other)
        await db.flush()
        db.add(_product(type("Tenant", (), {"tenant_id": other.id})(), "SKU-NEW"))
        await db.commit()
        count = await db.scalar(
            select(func.count(Product.id)).where(Product.normalized_sku == "SKU-NEW")
        )
        assert count == 2
        await db.delete(other)
        await db.commit()


@pytest.mark.asyncio
async def test_stock_clock_rejects_inverse_concurrent_and_late_events(
    integration_fixture,
) -> None:
    fixture = integration_fixture
    baseline = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with async_session_factory() as seed:
        product = _product(fixture, "CLOCK", stock=1)
        product.stock_updated_at = baseline
        seed.add(product)
        await seed.commit()
        product_id = product.id

    newer_at = baseline + timedelta(minutes=2)
    older_at = baseline + timedelta(minutes=1)
    async with async_session_factory() as newer_db, async_session_factory() as older_db:
        newer_integration = await _integration(newer_db, fixture, "agent")
        older_integration = await _integration(older_db, fixture, "agent")

        await apply_stock_sync(
            newer_db,
            integration=newer_integration,
            items=[StockUpdate(sku="CLOCK", stock=20, occurred_at=newer_at)],
        )
        older_task = asyncio.create_task(
            apply_stock_sync(
                older_db,
                integration=older_integration,
                items=[StockUpdate(sku="CLOCK", stock=10, occurred_at=older_at)],
            )
        )
        await asyncio.sleep(0.1)
        await newer_db.commit()
        await older_task
        await older_db.commit()

    async with async_session_factory() as check:
        product = await check.get(Product, product_id)
        assert product is not None
        assert product.stock == 20
        assert product.stock_updated_at == newer_at

        integration = await _integration(check, fixture, "agent")
        late = await apply_stock_sync(
            check,
            integration=integration,
            items=[StockUpdate(sku="CLOCK", stock=5, occurred_at=baseline)],
        )
        await check.commit()
        assert late["stale"] == 1
        await check.refresh(product)
        assert product.stock == 20


@pytest.mark.asyncio
async def test_null_zero_positive_and_invalid_payload_preserve_existing_data(
    integration_fixture,
) -> None:
    fixture = integration_fixture
    async with async_session_factory() as db:
        integration = await _integration(db, fixture, "webhook")
        products = [
            _product(fixture, "NULL", stock=7),
            _product(fixture, "ZERO", stock=7),
            _product(fixture, "POSITIVE", stock=7),
        ]
        db.add_all(products)
        await db.commit()

        from app.services.stock_sync import parse_stock_records

        items, errors = parse_stock_records(
            [
                {"sku": "NULL", "stock": None},
                {"sku": "ZERO", "stock": 0},
                {"sku": "POSITIVE", "stock": 9},
                {"sku": "POSITIVE", "stock": "inválido"},
            ]
        )
        result = await apply_stock_sync(
            db,
            integration=integration,
            items=items,
            extra_errors=errors,
        )
        await db.commit()
        db.expire_all()
        rows = (
            await db.execute(
                select(Product).where(Product.tenant_id == fixture.tenant_id)
            )
        ).scalars().all()
        values = {row.normalized_sku: row.stock for row in rows}
        assert values == {"NULL": 7, "ZERO": 0, "POSITIVE": 9}
        assert result["errors"] == 2
        assert result["status"] == "partial"


@pytest.mark.asyncio
async def test_transactional_idempotency_and_failure_after_claim(
    integration_fixture,
) -> None:
    fixture = integration_fixture
    async with async_session_factory() as db:
        integration = await _integration(db, fixture, "agent")
        repo = IntegrationRepository(db)
        run = await repo.create_sync(
            integration.id,
            fixture.tenant_id,
            "stock",
            trigger="agent",
        )
        payload = {"batch_id": "same-batch", "items": [{"sku": "A", "stock": 1}]}
        first, created_first = await repo.create_or_get_inbox(
            integration=integration,
            channel="agent",
            capability="stock",
            idempotency_key="same-batch",
            payload=payload,
            run_id=run.id,
        )
        second, created_second = await repo.create_or_get_inbox(
            integration=integration,
            channel="agent",
            capability="stock",
            idempotency_key="same-batch",
            payload=payload,
            run_id=run.id,
        )
        assert created_first is True and created_second is False
        assert first.id == second.id

        failed_run = await repo.create_sync(
            integration.id,
            fixture.tenant_id,
            "stock",
            trigger="test_failure_after_claim",
        )
        failed, _ = await repo.create_or_get_inbox(
            integration=integration,
            channel="unsupported",
            capability="stock",
            idempotency_key="claimed-before-failure",
            payload={"items": []},
            run_id=failed_run.id,
        )
        await db.commit()
        failed_id, failed_run_id = failed.id, failed_run.id

    await process_inbox_job({}, inbox_id=str(failed_id))
    async with async_session_factory() as db:
        inbox = await db.get(IntegrationInbox, failed_id)
        run = await db.get(SyncExecution, failed_run_id)
        assert inbox is not None and inbox.status == "dead_letter"
        assert inbox.attempts == 1 and inbox.processed_at is not None
        assert run is not None and str(run.status.value) == "failed"


@pytest.mark.asyncio
async def test_secret_rotation_keeps_only_the_previous_generation(
    integration_fixture,
) -> None:
    fixture = integration_fixture
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        integration = await _integration(db, fixture, "webhook")
        repo = IntegrationRepository(db)
        await repo.rotate_webhook_secret(
            integration, encrypted_secret="secret-v1", rotated_at=now
        )
        await repo.rotate_webhook_secret(
            integration,
            encrypted_secret="secret-v2",
            rotated_at=now + timedelta(seconds=1),
        )
        await db.commit()
        credential = await repo.get_webhook_credential(integration)
        assert isinstance(credential, IntegrationCredential)
        assert credential.payload == {"secret": "secret-v2"}
        assert credential.previous_payload == {"secret": "secret-v1"}


@pytest.mark.asyncio
async def test_reconciliation_detects_and_repairs_stock_divergence(
    integration_fixture, monkeypatch
) -> None:
    fixture = integration_fixture

    class Connector:
        async def fetch_stock(self):
            yield {"sku": "RECON", "stock": 12}

    monkeypatch.setattr(
        "app.services.api_pull.integration_registry.connector",
        lambda *args, **kwargs: Connector(),
    )
    monkeypatch.setattr(
        "app.services.api_pull.integration_registry.adapter",
        lambda *args, **kwargs: MappingStockAdapter(),
    )
    async with async_session_factory() as db:
        integration = await _integration(db, fixture, "api")
        product = _product(fixture, "RECON", stock=4)
        db.add(product)
        await db.commit()
        result = await fetch_and_apply_stock(
            db,
            integration=integration,
            config={"_connector": "test", "_max_records": 10},
        )
        await db.commit()
        await db.refresh(product)
        assert result["processed"] == 1
        assert product.stock == 12


@pytest.mark.asyncio
async def test_end_to_end_all_four_current_channels(
    integration_fixture, monkeypatch
) -> None:
    fixture = integration_fixture
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    async with async_session_factory() as db:
        db.add(_product(fixture, "FOUR", stock=0))
        await db.commit()

        agent = await _integration(db, fixture, "agent")
        await apply_stock_sync(
            db,
            integration=agent,
            items=[StockUpdate(sku="FOUR", stock=1, occurred_at=start)],
        )
        await db.commit()

        file_integration = await _integration(db, fixture, "file")
        csv = b"sku,stock,occurred_at\nFOUR,2,2026-02-01T00:01:00Z\n"
        await apply_stock_import(
            db,
            integration=file_integration,
            filename="stock.csv",
            content=csv,
        )
        await db.commit()

        webhook = await _integration(db, fixture, "webhook")
        event = WebhookEvent(
            tenant_id=fixture.tenant_id,
            integration_id=webhook.id,
            payload={"event": "stock.sync"},
            idempotency_key="four-webhook",
            received_at=datetime.now(timezone.utc),
        )
        db.add(event)
        await process_webhook_payload(
            db,
            webhook,
            event,
            {
                "event": "stock.sync",
                "records": [
                    {
                        "sku": "FOUR",
                        "stock": 3,
                        "occurred_at": "2026-02-01T00:02:00Z",
                    }
                ],
            },
        )
        await db.commit()

        class Connector:
            async def fetch_stock(self):
                yield {
                    "sku": "FOUR",
                    "stock": 4,
                    "occurred_at": "2026-02-01T00:03:00Z",
                }

        monkeypatch.setattr(
            "app.services.api_pull.integration_registry.connector",
            lambda *args, **kwargs: Connector(),
        )
        monkeypatch.setattr(
            "app.services.api_pull.integration_registry.adapter",
            lambda *args, **kwargs: MappingStockAdapter(),
        )
        api = await _integration(db, fixture, "api")
        await fetch_and_apply_stock(
            db,
            integration=api,
            config={"_connector": "test", "occurred_at_field": "occurred_at"},
        )
        await db.commit()

        product = (
            await db.execute(
                select(Product).where(
                    Product.tenant_id == fixture.tenant_id,
                    Product.normalized_sku == "FOUR",
                )
            )
        ).scalar_one()
        run_count = await db.scalar(
            select(func.count(SyncExecution.id)).where(
                SyncExecution.tenant_id == fixture.tenant_id
            )
        )
        assert product.stock == 4
        assert run_count == 4
        assert str(getattr(event.status, "value", event.status)) == "processed"
