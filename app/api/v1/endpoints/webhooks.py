"""Endpoint de webhook (Bloco 11 — seção 31).

- Chamado pelo ERP externo (SEM sessão) — a autenticação é a assinatura
  HMAC-SHA256 no header X-Webhook-Signature.
- Toda entrada é tratada como NÃO confiável (seção 41): limite de tamanho,
  JSON validado, IDs tipados.
- Ordem de segurança: assinatura -> integração -> rate limit -> replay -> processamento.
- Falhas NUNCA desaparecem: o evento WebhookEvent é registrado com status
  (processed/failed) mesmo quando o processamento falha (seção 33).
"""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from app.database.session import get_db
from app.models.enums import WebhookStatus
from app.repositories.integration import IntegrationRepository
from app.services.integration import (
    process_webhook_payload,
    verify_webhook_signature,
    webhook_idempotency,
    webhook_rate_limit,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Limite de tamanho do corpo (seção 41 — entrada externa não confiável)
MAX_BODY_BYTES = 256 * 1024  # 256 KiB

@router.post("/{integration_id}")
async def receive_webhook(
    integration_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Recebe evento do ERP: assinatura -> integração -> rate limit -> replay -> processa."""
    # 1) Corpo limitado (anti-DoS) e não vazio (seção 41)
    raw = await request.body()
    if not raw:
        raise ValidationError("Corpo vazio.")
    if len(raw) > MAX_BODY_BYTES:
        raise ValidationError("Corpo excede o limite permitido.")

    # 2) Autenticação por assinatura HMAC (seção 31) — antes de revelar a integração
    signature = request.headers.get("x-webhook-signature", "")
    if not await verify_webhook_signature(raw, signature):
        raise UnauthorizedError("Assinatura inválida.")

    # 3) Integração existe e está ativa
    repo = IntegrationRepository(db)
    integration = await repo.get(integration_id)
    if not integration or not integration.is_active:
        raise NotFoundError("Integração não encontrada.")

    # 4) Rate limit por integração (seção 31)
    if not await webhook_rate_limit(integration.id):
        raise RateLimitedError("Muitas requisições.")

    # 5) Proteção contra replay (seção 31)
    idem_key = request.headers.get("x-idempotency-key")
    if idem_key and not await webhook_idempotency(integration.id, idem_key):
        raise ConflictError("Evento duplicado (replay).")

    # 6) Validação do payload JSON (seção 41) — nunca deixa 500 vazar
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValidationError("Payload JSON inválido.")

    # 7) Registra o evento e processa (idempotente por external_id, seção 30).
    #    O evento é commitado ANTES do processamento para que a falha
    #    fique registrada mesmo se o processamento explodir (seção 33).
    event = await repo.create_webhook_event(
        integration.id, integration.tenant_id, payload
    )
    await db.commit()

    try:
        result = await process_webhook_payload(db, integration, event, payload)
        event.status = WebhookStatus.PROCESSED
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        # Falha registrada — nunca desaparece (seção 33)
        try:
            event.status = WebhookStatus.FAILED
            await db.commit()
        except Exception:
            await db.rollback()
        raise