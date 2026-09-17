"""Serviço de processamento de webhooks do Asaas.

- Valida o token de autenticação (ASAAS_WEBHOOK_TOKEN) enviado no header
  `asaas-access-token`.
- Mapeia eventos de pagamento para o nosso BillingStatus.
- Atualiza o status da cobrança local.
- Cria o registro local de cobrança quando o payment é gerado
  automaticamente por uma assinatura (não existe localmente ainda).
- Reativa a empresa quando a MENSALIDADE é paga.
- NUNCA levanta 422 para payload válido: eventos desconhecidos ou de
  criação (PAYMENT_CREATED) são apenas reconhecidos (acknowledged).
"""
import logging
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Company
from app.models.enums import BillingStatus, BillingType, CompanyStatus
from app.repositories.billing import BillingRepository
from app.repositories.company import CompanyRepository
from app.services.asaas import AsaasClient

logger = logging.getLogger("asaas_webhook")

# Eventos de pagamento do Asaas -> nosso BillingStatus.
PAYMENT_EVENTS = {
    "PAYMENT_CONFIRMED": BillingStatus.PAID,
    "PAYMENT_RECEIVED": BillingStatus.PAID,
    "PAYMENT_OVERDUE": BillingStatus.OVERDUE,
    "PAYMENT_REFUNDED": BillingStatus.REFUNDED,
    "PAYMENT_CANCELLED": BillingStatus.CANCELLED,
    "PAYMENT_DELETED": BillingStatus.CANCELLED,
}

# Eventos que NÃO alteram status (apenas reconhecer — sempre 200).
ACK_EVENTS = {
    "PAYMENT_CREATED",
    "PAYMENT_UPDATED",
    "PAYMENT_AUTHORIZED",
    "PAYMENT_ANTICIPATED",
    "PAYMENT_RESTORED",
    "PAYMENT_DUNNING_RECEIVED",
    "PAYMENT_BANK_SLIP_VIEWED",
    "PAYMENT_CHECKOUT_VIEWED",
}

# billingType do Asaas -> nosso BillingType.
_BILLING_TYPE_MAP = {
    "PIX": BillingType.PIX,
    "BOLETO": BillingType.BOLETO,
    "CREDIT_CARD": BillingType.CREDIT_CARD,
}


def validate_webhook_token(token: str | None) -> bool:
    """Valida o token de autenticação do webhook do Asaas.

    O Asaas envia o token no header `asaas-access-token`. Se o token
    configurado estiver vazio no .env, NENHUM webhook passa (fail-closed).
    """
    settings = get_settings()
    if not settings.ASAAS_WEBHOOK_TOKEN:
        return False
    return token == settings.ASAAS_WEBHOOK_TOKEN


async def _create_charge_from_payment(
    db: AsyncSession, payment: dict
) -> "BillingCharge | None":
    """Cria o registro local de cobrança a partir de um payment do Asaas.

    Necessário para payments gerados automaticamente por ASSINATURAS:
    o Asaas cria a cobrança mensal e o webhook precisa materializá-la
    localmente (histórico de mensalidades). Retorna a cobrança criada
    ou None se não for possível inferir a empresa/tipo.
    """
    # externalReference: "billing:{company_id}:{type}"
    ext = payment.get("externalReference") or ""
    parts = ext.split(":")
    if len(parts) < 3 or parts[0] != "billing":
        return None
    try:
        tenant_id = UUID(parts[1])
    except ValueError:
        return None
    charge_type = parts[2]

    due_date_raw = payment.get("dueDate")
    try:
        due_date = date.fromisoformat(due_date_raw) if due_date_raw else date.today()
    except ValueError:
        due_date = date.today()

    billing_type = _BILLING_TYPE_MAP.get(
        (payment.get("billingType") or "PIX").upper(), BillingType.PIX
    )

    repo = BillingRepository(db)
    charge = await repo.create(
        tenant_id=tenant_id,
        type=charge_type,
        value=payment.get("value") or 0,
        due_date=due_date,
        billing_type=billing_type,
        status=BillingStatus.PENDING,
        asaas_payment_id=payment.get("id"),
        asaas_subscription_id=payment.get("subscription"),
        checkout_url=AsaasClient.payment_checkout_url(payment),
        external_reference=ext,
    )
    return charge


async def process_asaas_webhook(db: AsyncSession, payload: dict) -> dict:
    """Processa o payload de um webhook do Asaas.

    Retorna um resumo do processamento para a resposta HTTP.
    NUNCA levanta exceção para payload válido — sempre retorna 200.
    """
    event = payload.get("event")
    if not event:
        return {"status": "ignored", "reason": "evento ausente"}

    # ---- Eventos de criação/atualização: apenas reconhece (200) ----
    if event in ACK_EVENTS:
        return {"status": "acknowledged", "event": event}

    # ---- Evento de pagamento (muda status) ----
    if event in PAYMENT_EVENTS:
        payment = payload.get("payment") or {}
        payment_id = payment.get("id")
        if not payment_id:
            return {"status": "ignored", "reason": "payment id ausente"}

        billing_repo = BillingRepository(db)
        # Busca SEM filtro de tenant (o webhook não tem sessão).
        charge = await billing_repo.get_by_asaas_payment_id_global(payment_id)

        # Payment gerado por assinatura ainda não materializado localmente.
        if not charge:
            charge = await _create_charge_from_payment(db, payment)
            if not charge:
                logger.warning(
                    "Webhook Asaas: payment %s sem cobrança local criável", payment_id
                )
                return {"status": "ignored", "reason": "cobrança não localizada"}

        new_status = PAYMENT_EVENTS[event]
        await billing_repo.update_status(charge, new_status)

        # Se a MENSALIDADE foi paga, reativa a empresa.
        if new_status == BillingStatus.PAID and charge.type == "mensalidade":
            company_repo = CompanyRepository(db)
            company = await company_repo.get(charge.tenant_id)
            if company and company.status != CompanyStatus.ACTIVE:
                await company_repo.set_status(company, CompanyStatus.ACTIVE)
                logger.info("Empresa %s reativada após pagamento da mensalidade", company.id)

        await db.commit()
        return {"status": "processed", "event": event, "charge_status": new_status.value}

    # ---- Evento de assinatura ----
    if event.startswith("SUBSCRIPTION_"):
        return {"status": "acknowledged", "event": event}

    # ---- Qualquer outro evento: reconhece (200), nunca 422 ----
    return {"status": "acknowledged", "event": event}