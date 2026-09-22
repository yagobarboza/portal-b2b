"""Sincronização canônica de cadastro de produtos."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.contracts import NormalizedProduct, normalize_sku
from app.models import ExternalEntityMapping, Product, SyncExecution
from app.models.enums import ProductStatus, SyncStatus
from app.repositories.catalog import invalidate_product_cache

ENTITY_TYPE = "product"
MAX_DETAILS = 50
PRODUCT_CHUNK_SIZE = 500


def _chunks(values: list, size: int = PRODUCT_CHUNK_SIZE):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _deduplicate(products: list[NormalizedProduct]) -> list[NormalizedProduct]:
    """Último registro por ID externo (ou SKU) vence dentro do lote."""
    unique: dict[tuple[str, str], NormalizedProduct] = {}
    for product in products:
        key = (
            ("external", product.external_id)
            if product.external_id
            else ("sku", product.sku)
        )
        unique[key] = product
    return list(unique.values())


def _new_product(tenant_id: UUID, item: NormalizedProduct) -> Product:
    return Product(
        tenant_id=tenant_id,
        sku=item.sku,
        normalized_sku=normalize_sku(item.sku),
        code=item.code,
        name=item.name,
        description=item.description,
        brand=item.brand,
        unit=item.unit,
        price=item.price if item.price is not None else Decimal(0),
        stock=item.stock,
        stock_updated_at=(item.occurred_at or datetime.now(timezone.utc))
        if item.stock is not None
        else None,
        image_url=item.image_url,
        status=ProductStatus(item.status or "active"),
    )


def _apply_product(product: Product, item: NormalizedProduct) -> bool:
    """Aplica somente campos suportados pelo catálogo; retorna se mudou."""
    values = {
        "sku": item.sku,
        "normalized_sku": normalize_sku(item.sku),
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "brand": item.brand,
        "unit": item.unit,
        "image_url": item.image_url,
    }
    if item.price is not None:
        values["price"] = item.price
    if item.stock is not None:
        values["stock"] = item.stock
        # Reprocessar o mesmo catálogo não deve fabricar uma nova versão de
        # estoque. O relógio só muda quando a origem forneceu um relógio ou o
        # saldo efetivamente mudou.
        if item.occurred_at is not None:
            values["stock_updated_at"] = item.occurred_at
        elif product.stock != item.stock:
            values["stock_updated_at"] = datetime.now(timezone.utc)
    if item.status is not None:
        values["status"] = ProductStatus(item.status)

    changed = bool(product.is_deleted)
    product.is_deleted = False
    product.deleted_at = None
    for field, value in values.items():
        if value is not None and getattr(product, field) != value:
            setattr(product, field, value)
            changed = True
    return changed


class ProductSyncService:
    """Upsert idempotente por mapping externo, com fallback por SKU canônico."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def sync(
        self,
        *,
        integration,
        products: list[NormalizedProduct],
        sync_execution: SyncExecution | None = None,
        input_errors: list[dict] | None = None,
    ) -> dict:
        tenant_id = integration.tenant_id
        now = datetime.now(timezone.utc)
        items = _deduplicate(products)
        external_ids = [p.external_id for p in items if p.external_id]
        normalized_skus = [p.sku for p in items]

        sync = sync_execution or SyncExecution(
            tenant_id=tenant_id,
            integration_id=integration.id,
            entity=ENTITY_TYPE,
        )
        if sync_execution is None:
            self.db.add(sync)
        sync.status = SyncStatus.RUNNING
        sync.started_at = sync.started_at or now
        sync.last_attempt_at = now
        await self.db.flush()

        mappings: list[ExternalEntityMapping] = []
        for external_chunk in _chunks(external_ids):
            result = await self.db.execute(
                select(ExternalEntityMapping).where(
                    ExternalEntityMapping.tenant_id == tenant_id,
                    ExternalEntityMapping.integration_id == integration.id,
                    ExternalEntityMapping.entity_type == ENTITY_TYPE,
                    ExternalEntityMapping.external_id.in_(external_chunk),
                )
            )
            mappings.extend(result.scalars().all())
        mapping_by_external = {m.external_id: m for m in mappings}

        mapped_ids = [m.internal_id for m in mappings]
        existing: list[Product] = []
        lookup_values = list(dict.fromkeys(normalized_skus + mapped_ids))
        # IDs e SKUs são buscados em chunks para não exceder parâmetros do banco.
        for lookup_chunk in _chunks(lookup_values):
            ids = [value for value in lookup_chunk if isinstance(value, UUID)]
            skus = [value for value in lookup_chunk if isinstance(value, str)]
            filters = []
            if ids:
                filters.append(Product.id.in_(ids))
            if skus:
                filters.append(Product.normalized_sku.in_(skus))
            result = await self.db.execute(
                select(Product).where(
                    Product.tenant_id == tenant_id,
                    or_(*filters),
                )
            )
            existing.extend(result.scalars().all())
        by_id = {p.id: p for p in existing}
        by_sku = {p.normalized_sku: p for p in existing}

        created = 0
        updated = 0
        unchanged = 0
        errors = len(input_errors or [])
        details = list(input_errors or [])[:MAX_DETAILS]
        pending_mappings: list[tuple[str, Product]] = []

        for item in items:
            mapping = (
                mapping_by_external.get(item.external_id)
                if item.external_id
                else None
            )
            product = by_id.get(mapping.internal_id) if mapping else None
            if mapping and product is None:
                errors += 1
                if len(details) < MAX_DETAILS:
                    details.append(
                        {
                            "sku": item.sku,
                            "external_id": item.external_id,
                            "error": "Mapping externo aponta para produto inexistente no tenant.",
                        }
                    )
                continue
            product = product or by_sku.get(item.sku)

            if product is None:
                product = _new_product(tenant_id, item)
                self.db.add(product)
                created += 1
            elif _apply_product(product, item):
                updated += 1
            else:
                unchanged += 1
            by_sku[item.sku] = product

            if item.external_id and mapping is None:
                pending_mappings.append((item.external_id, product))

        # Um único flush materializa os IDs de todos os produtos novos.
        await self.db.flush()
        mapping_rows = [
            {
                "tenant_id": tenant_id,
                "integration_id": integration.id,
                "entity_type": ENTITY_TYPE,
                "external_id": external_id,
                "internal_id": product.id,
            }
            for external_id, product in pending_mappings
        ]
        for mapping_chunk in _chunks(mapping_rows):
            await self.db.execute(
                pg_insert(ExternalEntityMapping)
                .values(mapping_chunk)
                .on_conflict_do_nothing(
                    constraint="uq_external_entity_mappings_external"
                )
            )
        await self.db.flush()
        if created or updated:
            await invalidate_product_cache(tenant_id)

        processed = created + updated
        if errors == 0:
            sync.status = SyncStatus.SUCCESS
            label = "ok"
        elif processed or unchanged:
            sync.status = SyncStatus.PARTIAL
            label = "partial"
        else:
            sync.status = SyncStatus.FAILED
            label = "failed"
        sync.processed = processed
        sync.errors = errors
        sync.finished_at = datetime.now(timezone.utc)
        sync.terminal_at = sync.finished_at
        sync.next_retry_at = None
        sync.message = (
            f"{created} criado(s), {updated} atualizado(s), "
            f"{unchanged} inalterado(s), {errors} erro(s)."
        )
        result = {
            "sync_id": sync.id,
            "status": label,
            "processed": processed,
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "errors": errors,
            "message": sync.message,
            "details": details,
        }
        from app.services.integration_observability import finish_run_from_result

        finish_run_from_result(
            sync,
            result,
            items_received=len(products) + len(input_errors or []),
        )
        await self.db.flush()
        result["message"] = sync.message
        result["details"] = sync.item_errors or []
        return result
