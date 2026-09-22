"""Serviço de integrações (Bloco 11 — seções 28-33).

- Assinatura HMAC-SHA256 para webhooks (seção 31).
- Rate limit via Redis; replay/idempotência ficam na inbox PostgreSQL.
- Upsert por external_id — idempotência obrigatória (seção 30).
- Execuções de sync com status/quantidades/erros (seção 33).
- BLOCO B3: evento `stock.sync` processa ESTOQUE via `apply_stock_sync`
  (mesmo motor do agente/arquivo — normalização, sem criar produto).
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import redis.asyncio as aioredis
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_str
from app.integrations.adapters import MappingProductAdapter
from app.models import Customer, FinancialAccount
from app.models.enums import FinancialAccountStatus, SyncStatus
from app.services.product_sync import ProductSyncService
from app.services.stock_sync import apply_stock_sync, parse_stock_records

settings = get_settings()
redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

# ---------- Assinatura / replay / rate limit (seção 31) ----------
def generate_webhook_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"

def sign_webhook(
    body: bytes,
    secret: str,
    integration_id: UUID,
    timestamp: str,
) -> str:
    signed = f"{timestamp}.{integration_id}.".encode("utf-8") + body
    return "sha256=" + hmac.new(
        secret.encode(), signed, hashlib.sha256
    ).hexdigest()

async def verify_webhook_signature(
    body: bytes,
    signature: str,
    timestamp: str,
    integration,
    credential,
) -> bool:
    payload = dict(credential.payload or {}) if credential else {}
    encrypted_secret = payload.get("secret")
    if not signature or not timestamp or not encrypted_secret:
        return False
    try:
        sent_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return False
    now = datetime.now(timezone.utc)
    if abs((now - sent_at).total_seconds()) > settings.WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS:
        return False

    try:
        candidates = [decrypt_str(encrypted_secret)]
        rotated_at = credential.rotated_at
        if rotated_at and rotated_at.tzinfo is None:
            rotated_at = rotated_at.replace(tzinfo=timezone.utc)
        previous_valid = (
            credential.previous_payload
            and rotated_at
            and now <= rotated_at + timedelta(
                seconds=settings.WEBHOOK_SECRET_ROTATION_GRACE_SECONDS
            )
        )
        if previous_valid:
            previous = dict(credential.previous_payload or {}).get("secret")
            if previous:
                candidates.append(decrypt_str(previous))
    except Exception:  # segredo ausente/corrompido nunca vira erro 500 público
        return False
    return any(
        hmac.compare_digest(
            sign_webhook(body, secret, integration.id, timestamp), signature
        )
        for secret in candidates
    )

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
            customer_uuid = UUID(str(customer_id))
            customer_result = await db.execute(
                select(Customer.id).where(
                    Customer.id == customer_uuid,
                    Customer.tenant_id == tenant_id,
                    Customer.is_deleted.is_(False),
                )
            )
            if customer_result.scalar_one_or_none() is None:
                errors += 1
                last_msg = "Cliente não encontrado no tenant da integração"
                continue
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
                    customer_id=customer_uuid,
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

def normalize_product_records(records: list[dict]) -> tuple[list, list[dict]]:
    """Normaliza um lote externo sem deixar um item inválido abortar os demais."""
    adapter = MappingProductAdapter()
    products = []
    errors: list[dict] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append({"index": index, "error": "Registro não é um objeto."})
            continue
        try:
            products.append(adapter.adapt_product(record))
        except PydanticValidationError as exc:
            message = exc.errors()[0].get("msg", "Produto inválido.")
            errors.append({"index": index, "error": str(message)[:200]})
    return products, errors


async def upsert_products(
    db: AsyncSession,
    integration,
    records: list[dict],
    *,
    sync_execution=None,
) -> dict:
    products, input_errors = normalize_product_records(records)
    return await ProductSyncService(db).sync(
        integration=integration,
        products=products,
        sync_execution=sync_execution,
        input_errors=input_errors,
    )

# ---------- Runner de sincronização (seções 32/33) ----------
async def run_sync(
    db: AsyncSession, integration, entity: str, records: list[dict] | None = None
) -> object:
    """Executa uma sincronização e registra SyncExecution (seção 33).

    Idempotente (seção 30): upsert por external_id — rodar 2x não duplica.
    """
    from app.models import SyncExecution

    if records is None:
        raise RuntimeError(
            "Sincronização completa desabilitada até existir um connector configurado."
        )

    sync = SyncExecution(
        tenant_id=integration.tenant_id,
        integration_id=integration.id,
        entity=entity,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        last_attempt_at=datetime.now(timezone.utc),
        trigger="internal",
    )
    db.add(sync)
    await db.flush()

    try:
        if entity == "financial":
            processed, errors, msg = await upsert_financial_accounts(
                db, integration.tenant_id, records
            )
        elif entity in {"product", "products"}:
            product_result = await upsert_products(
                db, integration, records, sync_execution=sync
            )
            processed = product_result["processed"]
            errors = product_result["errors"]
            msg = product_result["message"]
        else:
            raise ValueError(f"Entidade não suportada: {entity}")

        if entity == "financial":
            sync.status = SyncStatus.SUCCESS if not errors else SyncStatus.PARTIAL
            sync.processed = processed
            sync.errors = errors
            sync.message = msg or "Sincronização concluída"
            result_data = {
                "processed": processed,
                "errors": errors,
                "message": sync.message,
                "details": [],
            }
            from app.services.integration_observability import finish_run_from_result

            sync.finished_at = datetime.now(timezone.utc)
            sync.terminal_at = sync.finished_at
            finish_run_from_result(sync, result_data, items_received=len(records))
    except Exception as exc:  # noqa: BLE001 — falha registrada, nunca some (seção 33)
        from app.services.integration_observability import set_run_failure

        set_run_failure(
            sync,
            status="failed",
            code=exc.__class__.__name__,
            exc=exc,
            retryable=False,
        )

    if sync.finished_at is None:
        sync.finished_at = datetime.now(timezone.utc)
        sync.terminal_at = sync.finished_at
    await db.commit()
    result = await db.execute(
        select(SyncExecution).where(SyncExecution.id == sync.id)
    )
    return result.scalars().first()

async def process_webhook_payload(
    db: AsyncSession, integration, event, payload: dict, *, sync_execution=None
) -> dict:
    """Processa o payload do webhook e atualiza o evento (seção 31).

    Eventos suportados:
    - financial.sync → títulos financeiros (upsert por external_id).
    - product.sync   → produtos completos (upsert por sku).
    - stock.sync     → SOMENTE estoque (Bloco B3 — tipo `webhook`).
      Reusa o motor de estoque (`apply_stock_sync`): normaliza para inteiro,
      só atualiza produto existente, registra SyncExecution e aceita
      registros inválidos sem derrubar o lote.
    """
    event_name = payload.get("event", "")
    records = payload.get("records", []) or []
    if not isinstance(records, list):
        raise ValueError("records deve ser uma lista")
    supported_events = {"financial.sync", "product.sync", "stock.sync"}
    if event_name not in supported_events:
        raise ValueError(f"Evento não suportado: {event_name}")
    from app.models import SyncExecution
    from app.services.integration_observability import finish_run_from_result

    entity = {
        "financial.sync": "financial",
        "product.sync": "products",
        "stock.sync": "stock",
    }.get(event_name, "unknown")
    sync = sync_execution or SyncExecution(
        tenant_id=integration.tenant_id,
        integration_id=integration.id,
        entity=entity,
        trigger="webhook",
    )
    if sync_execution is None:
        db.add(sync)
    sync.status = SyncStatus.RUNNING
    sync.started_at = sync.started_at or datetime.now(timezone.utc)
    sync.last_attempt_at = datetime.now(timezone.utc)
    await db.flush()

    if event_name == "financial.sync":
        processed, errors, msg = await upsert_financial_accounts(
            db, integration.tenant_id, records
        )
        sync.status = SyncStatus.SUCCESS if not errors else (
            SyncStatus.PARTIAL if processed else SyncStatus.FAILED
        )
        sync.processed = processed
        sync.errors = errors
        sync.finished_at = datetime.now(timezone.utc)
        sync.terminal_at = sync.finished_at
        result = {
            "sync_id": sync.id,
            "status": str(getattr(sync.status, "value", sync.status)),
            "processed": processed,
            "errors": errors,
            "message": msg,
            "details": [],
        }
        finish_run_from_result(sync, result, items_received=len(records))
    elif event_name == "product.sync":
        result = await upsert_products(
            db, integration, records, sync_execution=sync
        )
        processed = result["processed"]
        errors = result["errors"]
        msg = result["message"]
    elif event_name == "stock.sync":
        items, row_errors = parse_stock_records(records)
        result = await apply_stock_sync(
            db,
            integration=integration,
            items=items,
            batch_id=None,
            extra_errors=row_errors,
            sync_execution=sync,
        )
        processed = result["processed"]
        errors = result["errors"]
        msg = result["message"]
    event.status = "processed"
    event.processed_at = datetime.now(timezone.utc)
    event.error = msg if errors else None
    await db.flush()
    return {
        "status": "processed",
        "run_id": str(sync.id),
        "processed": processed,
        "errors": errors,
    }
