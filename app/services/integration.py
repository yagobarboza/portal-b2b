"""Serviço de integrações (Bloco 11 — seções 28-33).

- Assinatura HMAC-SHA256 para webhooks (seção 31).
- Rate limit + proteção contra replay via Redis (seção 31).
- Upsert por external_id — idempotência obrigatória (seção 30).
- Execuções de sync com status/quantidades/erros (seção 33).
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import FinancialAccount, Product
from app.models.enums import FinancialAccountStatus, ProductStatus, SyncStatus

settings = get_settings()
redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

# ---------- Assinatura / replay / rate limit (seção 31) ----------
def sign_webhook(body: bytes, secret: str | None = None) -> str:
    secret = secret if secret is not None else settings.WEBHOOK_SECRET
    return "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()

async def verify_webhook_signature(body: bytes, signature: str) -> bool:
    if not settings.WEBHOOK_SECRET or not signature:
        return False
    expected = sign_webhook(body, settings.WEBHOOK_SECRET)
    return hmac.compare_digest(expected, signature)

async def webhook_rate_limit(integration_id: UUID) -> bool:
    """Limite de eventos/minuto por integração (fail-open se Redis indisponível)."""
    try:
        key = f"rl:webhook:{integration_id}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 60)
        return count <= settings.WEBHOOK_RATE_LIMIT
    except Exception:
        return True

async def webhook_idempotency(integration_id: UUID, idem_key: str) -> bool:
    """Proteção contra replay: a mesma chave só é aceita uma vez (seção 31)."""
    try:
        key = f"idem:webhook:{integration_id}:{idem_key}"
        return bool(
            await redis_client.set(
                key, "1", nx=True, ex=settings.WEBHOOK_IDEMPOTENCY_TTL
            )
        )
    except Exception:
        return True

# ---------- Helpers ----------
def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

def _set_if_exists(obj, field: str, value):
    if value is None:
        return
    if hasattr(obj, field):
        setattr(obj, field, value)

# ---------- Upserts idempotentes (seção 30) ----------
async def upsert_financial_accounts(
    db: AsyncSession, tenant_id: UUID, records: list[dict]
) -> tuple[int, int, str]:
    processed = 0
    errors = 0
    last_msg = ""
    for rec in records:
        ext = rec.get("external_id")
        customer_id = rec.get("customer_id")
        if not ext:
            errors += 1
            last_msg = "Registro sem external_id"
            continue
        if not customer_id:
            errors += 1
            last_msg = "Registro sem customer_id"
            continue
        try:
            result = await db.execute(
                select(FinancialAccount).where(
                    FinancialAccount.tenant_id == tenant_id,
                    FinancialAccount.external_id == ext,
                )
            )
            account = result.scalars().first()
            if account is None:
                account = FinancialAccount(
                    tenant_id=tenant_id,
                    customer_id=UUID(str(customer_id)),
                    document=str(rec.get("document") or ext),
                    value=Decimal(str(rec.get("value") or "0")),
                    due_date=_parse_dt(rec.get("due_date")) or datetime.now(timezone.utc),
                    status=FinancialAccountStatus(rec.get("status", "open")),
                    paid_at=_parse_dt(rec.get("paid_at")),
                    external_id=ext,
                )
                db.add(account)
            else:
                _set_if_exists(account, "document", rec.get("document"))
                if rec.get("value") is not None:
                    account.value = Decimal(str(rec["value"]))
                if rec.get("due_date"):
                    account.due_date = _parse_dt(rec["due_date"])
                if rec.get("status"):
                    account.status = FinancialAccountStatus(rec["status"])
                if rec.get("paid_at"):
                    account.paid_at = _parse_dt(rec["paid_at"])
            processed += 1
        except Exception as exc:  # noqa: BLE001 — registro isolado não derruba o lote
            errors += 1
            last_msg = str(exc)[:200]
    await db.flush()
    return processed, errors, last_msg

async def upsert_products(
    db: AsyncSession, tenant_id: UUID, records: list[dict]
) -> tuple[int, int, str]:
    processed = 0
    errors = 0
    last_msg = ""
    for rec in records:
        sku = rec.get("sku")
        if not sku:
            errors += 1
            last_msg = "Registro sem sku"
            continue
        try:
            result = await db.execute(
                select(Product).where(
                    Product.tenant_id == tenant_id,
                    Product.sku == sku,
                )
            )
            product = result.scalars().first()
            if product is None:
                fields = {"tenant_id": tenant_id, "sku": sku}
                if hasattr(Product, "name"):
                    fields["name"] = rec.get("name") or sku
                if hasattr(Product, "price") and rec.get("price") is not None:
                    fields["price"] = Decimal(str(rec["price"]))
                if hasattr(Product, "stock") and rec.get("stock") is not None:
                    fields["stock"] = int(rec["stock"])
                if hasattr(Product, "status") and rec.get("status"):
                    fields["status"] = ProductStatus(rec["status"])
                product = Product(**fields)
                db.add(product)
            else:
                _set_if_exists(product, "name", rec.get("name"))
                if rec.get("price") is not None and hasattr(product, "price"):
                    product.price = Decimal(str(rec["price"]))
                if rec.get("stock") is not None and hasattr(product, "stock"):
                    product.stock = int(rec["stock"])
                if rec.get("status") and hasattr(product, "status"):
                    product.status = ProductStatus(rec["status"])
            processed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            last_msg = str(exc)[:200]
    await db.flush()
    return processed, errors, last_msg

# ---------- Dados de demonstração (simulam o ERP, seção 29) ----------
def _demo_records(entity: str, customer_id: UUID | None = None) -> list[dict]:
    if entity == "financial":
        now = datetime.now(timezone.utc)
        records = [
            {"external_id": "ERP-SYNC-001", "document": "NF 3001", "value": "2200.00",
             "due_date": (now + timedelta(days=10)).isoformat(), "status": "open"},
            {"external_id": "ERP-SYNC-002", "document": "NF 3002", "value": "950.00",
             "due_date": (now - timedelta(days=5)).isoformat(), "status": "overdue"},
            {"external_id": "ERP-SYNC-003", "document": "NF 3003", "value": "400.00",
             "due_date": (now - timedelta(days=20)).isoformat(),
             "status": "paid", "paid_at": (now - timedelta(days=18)).isoformat()},
        ]
        for r in records:
            if customer_id:
                r["customer_id"] = str(customer_id)
        return records
    if entity == "product":
        return [
            {"sku": "SKU-SYNC-001", "name": "Produto Sincronizado 1",
             "price": "45.90", "stock": 100, "status": "active"},
            {"sku": "SKU-SYNC-002", "name": "Produto Sincronizado 2",
             "price": "120.00", "stock": 50, "status": "active"},
        ]
    return []

# ---------- Runner de sincronização (seções 32/33) ----------
async def run_sync(
    db: AsyncSession, integration, entity: str, records: list[dict] | None = None
) -> object:
    """Executa uma sincronização e registra SyncExecution (seção 33).

    Idempotente (seção 30): upsert por external_id — rodar 2x não duplica.
    """
    from app.models import SyncExecution

    sync = SyncExecution(
        tenant_id=integration.tenant_id,
        integration_id=integration.id,
        entity=entity,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(sync)
    await db.flush()

    try:
        if records is None:
            customer_id = None
            if entity == "financial":
                from app.models import Customer
                result = await db.execute(
                    select(Customer)
                    .where(Customer.tenant_id == integration.tenant_id)
                    .limit(1)
                )
                customer = result.scalars().first()
                customer_id = customer.id if customer else None
            records = _demo_records(entity, customer_id)

        if entity == "financial":
            processed, errors, msg = await upsert_financial_accounts(
                db, integration.tenant_id, records
            )
        elif entity == "product":
            processed, errors, msg = await upsert_products(
                db, integration.tenant_id, records
            )
        else:
            raise ValueError(f"Entidade não suportada: {entity}")

        sync.status = SyncStatus.SUCCESS
        sync.processed = processed
        sync.errors = errors
        sync.message = msg or "Sincronização concluída"
    except Exception as exc:  # noqa: BLE001 — falha registrada, nunca some (seção 33)
        sync.status = SyncStatus.FAILED
        sync.message = str(exc)[:500]

    sync.finished_at = datetime.now(timezone.utc)
    await db.commit()

    result = await db.execute(
        select(SyncExecution).where(SyncExecution.id == sync.id)
    )
    return result.scalars().first()

async def process_webhook_payload(
    db: AsyncSession, integration, event, payload: dict
) -> dict:
    """Processa o payload do webhook e atualiza o evento (seção 31)."""
    from app.models import WebhookEvent

    event_name = payload.get("event", "")
    records = payload.get("records", []) or []
    try:
        if event_name == "financial.sync":
            processed, errors, msg = await upsert_financial_accounts(
                db, integration.tenant_id, records
            )
        elif event_name == "product.sync":
            processed, errors, msg = await upsert_products(
                db, integration.tenant_id, records
            )
        else:
            raise ValueError(f"Evento não suportado: {event_name}")

        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        event.error = msg or None
        await db.flush()
        return {"status": "processed", "processed": processed, "errors": errors}
    except Exception as exc:  # noqa: BLE001
        event.status = "failed"
        event.processed_at = datetime.now(timezone.utc)
        event.error = str(exc)[:500]
        await db.flush()
        return {"status": "failed", "processed": 0, "errors": len(records), "error": str(exc)[:200]}