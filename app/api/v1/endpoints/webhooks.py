"""Endpoint de webhook (Bloco 11 — seção 31).

- Chamado pelo ERP externo (SEM sessão) — a autenticação é a assinatura
  HMAC-SHA256 no header X-Webhook-Signature.
- Toda entrada é tratada como NÃO confiável (seção 41): limite de tamanho,
  JSON validado, IDs tipados.
- Ordem: assinatura -> integração -> rate limit -> inbox transacional -> ACK -> fila.
- Falhas NUNCA desaparecem: o evento WebhookEvent é registrado com status
  e a inbox termina em succeeded/dead_letter sem perda silenciosa.
"""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from app.core.config import get_settings
from app.database.session import get_db
from app.core.queue import enqueue_job
from app.repositories.integration import IntegrationRepository
from app.schemas.integration import InboxAccepted
from app.services.integration import (
    verify_webhook_signature,
    webhook_rate_limit,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
settings = get_settings()

# Limite de tamanho do corpo (seção 41 — entrada externa não confiável)
MAX_BODY_BYTES = 256 * 1024  # 256 KiB

@router.post("/{integration_id}", response_model=InboxAccepted, status_code=202)
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

    # 2) Resolve a integração para obter o segredo individual. A resposta de
    # autenticação permanece genérica para não revelar existência/configuração.
    repo = IntegrationRepository(db)
    integration = await repo.get(integration_id)
    if not integration or not integration.is_active or integration.type != "webhook":
        raise UnauthorizedError("Assinatura inválida.")

    # 3) Autenticação HMAC com timestamp e integration_id vinculados à assinatura.
    signature = request.headers.get("x-webhook-signature", "")
    timestamp = request.headers.get("x-webhook-timestamp", "")
    credential = await repo.get_webhook_credential(integration)
    if not await verify_webhook_signature(
        raw, signature, timestamp, integration, credential
    ):
        raise UnauthorizedError("Assinatura inválida.")

    # 4) Rate limit por integração (seção 31)
    if not await webhook_rate_limit(integration.id):
        raise RateLimitedError("Muitas requisições.")

    # 5) A chave é obrigatória e persistida no PostgreSQL. Duplicatas
    # processadas recebem ACK idempotente, sem estimular retries infinitos.
    idem_key = (request.headers.get("x-idempotency-key") or "").strip()
    if not idem_key or len(idem_key) > 200:
        raise ValidationError("X-Idempotency-Key ausente ou inválido.")

    # 6) Validação do payload JSON (seção 41) — nunca deixa 500 vazar
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValidationError("Payload JSON inválido.")
    if not isinstance(payload, dict):
        raise ValidationError("Payload JSON deve ser um objeto.")

    try:
        inbox, created = await repo.create_or_get_inbox(
            integration=integration,
            channel="webhook",
            capability=str(payload.get("event") or "unknown"),
            idempotency_key=idem_key,
            payload=payload,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    if created:
        event_name = str(payload.get("event") or "unknown")
        entity = {
            "stock.sync": "stock",
            "product.sync": "products",
            "financial.sync": "financial",
        }.get(event_name, "unknown")
        run = await repo.create_sync(
            integration.id,
            integration.tenant_id,
            entity,
            trigger="webhook",
            correlation_id=idem_key[:120],
            request_size_bytes=len(raw),
            run_metadata={"event": event_name[:50]},
        )
        inbox.run_id = run.id
        event = await repo.create_webhook_event(
            integration.id,
            integration.tenant_id,
            payload,
            idem_key,
            inbox_id=inbox.id,
        )
        await db.commit()
        # A queda do Redis não perde o evento: o dispatcher periódico busca
        # toda inbox pendente. O ACK depende apenas do commit no PostgreSQL.
        await enqueue_job(
            "process_inbox_job", inbox_id=str(inbox.id)
        )
    else:
        event = await repo.get_webhook_event_by_idempotency(integration.id, idem_key)
        await db.commit()
    return InboxAccepted(
        status="accepted" if created else "duplicate",
        event_id=event.id if event else None,
        inbox_id=inbox.id,
    )
