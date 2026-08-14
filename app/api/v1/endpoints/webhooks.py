"""Endpoint de webhook (Bloco 11 — seção 31).

- Chamado pelo ERP externo (SEM sessão) — a autenticação é a assinatura
  HMAC-SHA256 no header X-Webhook-Signature.
- Rate limit por integração + proteção contra replay (X-Idempotency-Key).
- Cada evento é registrado em WebhookEvent (logs/tratamento de erros).
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, UnauthorizedError
from app.database.session import get_db
from app.repositories.integration import IntegrationRepository
from app.services.integration import (
    process_webhook_payload,
    verify_webhook_signature,
    webhook_idempotency,
    webhook_rate_limit,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

@router.post("/{integration_id}")
async def receive_webhook(
    integration_id,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Recebe evento do ERP: assinatura -> rate limit -> replay -> processa."""
    body_bytes = await request.body()

    # 1) Autenticação por assinatura (seção 31)
    signature = request.headers.get("x-webhook-signature", "")
    if not await verify_webhook_signature(body_bytes, signature):
        raise UnauthorizedError("Assinatura inválida.")

    # 2) Integração existe e está ativa
    repo = IntegrationRepository(db)
    integration = await repo.get(integration_id)
    if not integration or not integration.is_active:
        raise NotFoundError("Integração não encontrada.")

    # 3) Rate limit por integração (seção 31)
    if not await webhook_rate_limit(integration.id):
        raise HTTPException(status_code=429, detail="Rate limit excedido.")

    # 4) Proteção contra replay (seção 31)
    idem_key = request.headers.get("x-idempotency-key")
    if idem_key and not await webhook_idempotency(integration.id, idem_key):
        raise HTTPException(status_code=409, detail="Evento duplicado (replay).")

    # 5) Registra o evento e processa (idempotente por external_id, seção 30)
    payload = json.loads(body_bytes.decode("utf-8"))
    event = await repo.create_webhook_event(
        integration.id, integration.tenant_id, payload   # <-- tenant_id adicionado
    )
    await db.commit()
    result = await process_webhook_payload(db, integration, event, payload)
    await db.commit()
    return result