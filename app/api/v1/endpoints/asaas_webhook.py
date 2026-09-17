"""Endpoint de webhook do Asaas (pagamentos/assinaturas).

- Chamado pelo Asaas (SEM sessão) — autenticação via token no header
  `asaas-access-token`.
- Ordem de segurança: corpo -> token -> rate limit -> idempotência -> processa.
- Toda entrada é tratada como NÃO confiável (anti-DoS).
"""
import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RateLimitedError, UnauthorizedError, ValidationError
from app.core.rate_limit import asaas_idempotency, asaas_rate_limit
from app.database.session import get_db
from app.services.asaas_webhook import (
    process_asaas_webhook,
    validate_webhook_token,
)

router = APIRouter(prefix="/webhooks/asaas", tags=["Asaas Webhook"])

# Limite de tamanho do corpo (anti-DoS).
MAX_BODY_BYTES = 256 * 1024  # 256 KiB


@router.post("")
async def receive_asaas_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Recebe evento do Asaas: corpo -> token -> rate limit -> idempotência -> processa."""
    # 1) Corpo limitado e não vazio (anti-DoS)
    raw = await request.body()
    if not raw:
        raise ValidationError("Corpo vazio.")
    if len(raw) > MAX_BODY_BYTES:
        raise ValidationError("Corpo excede o limite permitido.")

    # 2) Autenticação por token (header asaas-access-token)
    token = request.headers.get("asaas-access-token", "")
    if not validate_webhook_token(token):
        raise UnauthorizedError("Token inválido.")

    # 3) JSON válido (nunca deixa 500 vazar)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValidationError("Payload JSON inválido.")

    # 4) Rate limit global do webhook Asaas (fail-open se Redis indisponível)
    if not await asaas_rate_limit():
        raise RateLimitedError("Muitas requisições.")

    # 5) Idempotência — a doc do Asaas recomenda usar o `id` do EVENTO
    #    (campo "id" no topo do payload, ex.: "evt_..."). Fallback para
    #    event + resource id quando o id do evento vier vazio.
    event = payload.get("event", "")
    event_id = payload.get("id") or ""
    resource = payload.get("payment") or payload.get("subscription") or {}
    resource_id = resource.get("id") or ""
    idem_key = event_id or f"{event}:{resource_id}"
    if not await asaas_idempotency(event, idem_key):
        # Já processado: responde 200 para o Asaas não reenviar em loop.
        return {"status": "duplicate", "event": event}

    # 6) Processa
    result = await process_asaas_webhook(db, payload)
    return result