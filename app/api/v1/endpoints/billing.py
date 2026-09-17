"""Endpoints de cobranças/assinaturas (integração Asaas).
- Super Admin: cria cobranças avulsas (implantação/custom/módulo) e assinaturas
  (mensalidade recorrente), e lista a situação financeira de TODAS as empresas.
- Admin da empresa: vê APENAS as cobranças do seu tenant (isolamento) e
  acessa a página de pagamento hospedada do Asaas.
Identificação da empresa na criação: aceita `company_id` (UUID) OU
`company_cnpj` (com ou sem máscara). Deve informar exatamente um.
"""
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_permission
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.core.permissions import BILLING_MANAGE
from app.database.session import get_db
from app.models import User
from app.models.enums import BillingStatus
from app.repositories.billing import BillingRepository
from app.repositories.company import CompanyRepository
from app.schemas.billing import (
    BillingChargeCreate,
    BillingChargePage,
    BillingChargeRead,
    BillingSubscriptionCreate,
)
from app.services.asaas import AsaasClient, AsaasError
from app.services.audit import record_audit

router = APIRouter(prefix="/billing", tags=["Billing"])


def _is_company_admin(user: User) -> bool:
    """Admin da empresa (tenant) — não é superadmin e não é cliente."""
    return not user.is_super_admin and user.customer_id is None


async def _resolve_company(
    db: AsyncSession,
    company_id: UUID | None,
    company_cnpj: str | None,
):
    """Resolve a empresa por ID OU CNPJ (exatamente um deve ser informado)."""
    if bool(company_id) == bool(company_cnpj):
        raise ValidationFailedError(
            "Informe exatamente um identificador: company_id OU company_cnpj."
        )
    repo = CompanyRepository(db)
    if company_id:
        company = await repo.get(company_id)
    else:
        company = await repo.get_by_cnpj(company_cnpj or "")
    if not company:
        raise NotFoundError("Empresa não encontrada (verifique o CNPJ/ID).")
    return company


@router.get("/charges", response_model=BillingChargePage)
async def list_my_charges(
    status: BillingStatus | None = None,
    type: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BillingChargePage:
    """Admin da empresa: lista as cobranças do SEU tenant (com filtros)."""
    if not _is_company_admin(user):
        raise ValidationFailedError("Acesso negado.")
    repo = BillingRepository(db)
    items, total = await repo.list_for_tenant(
        tenant_id=user.tenant_id, status=status, type=type, page=page, page_size=page_size
    )
    pages = (total + page_size - 1) // page_size
    return BillingChargePage(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/charges/all", response_model=BillingChargePage)
async def list_all_charges(
    status: BillingStatus | None = None,
    type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(BILLING_MANAGE)),
) -> BillingChargePage:
    """Super Admin: situação financeira de TODAS as empresas (filtros)."""
    repo = BillingRepository(db)
    items, total = await repo.list_all(
        status=status, type=type, search=search, page=page, page_size=page_size
    )
    pages = (total + page_size - 1) // page_size
    return BillingChargePage(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.post("/charges", response_model=BillingChargeRead, status_code=201)
async def create_charge(
    body: BillingChargeCreate,
    company_id: UUID | None = None,
    company_cnpj: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(BILLING_MANAGE)),
) -> BillingChargeRead:
    """Super Admin: cria cobrança AVULSA (implantação/custom/módulo).
    Identifica a empresa por `company_id` OU `company_cnpj`.
    """
    company = await _resolve_company(db, company_id, company_cnpj)
    try:
        client = AsaasClient()
        customer_id = company.asaas_customer_id
        if not customer_id:
            customer_id = await client.get_or_create_customer(
                name=company.name, cpf_cnpj=company.cnpj
            )
            company.asaas_customer_id = customer_id
            await db.flush()
        external_reference = f"billing:{company.id}:{body.type}"
        description = body.description or f"{body.type} — {company.name}"
        payment = await client.create_payment(
            customer_id=customer_id,
            value=float(body.value),
            due_date=body.due_date.isoformat(),
            billing_type=body.billing_type.value.upper(),
            description=description,
            external_reference=external_reference,
        )
    except AsaasError as exc:
        raise ValidationFailedError(str(exc)) from exc
    repo = BillingRepository(db)
    try:
        charge = await repo.create(
            tenant_id=company.id,
            type=body.type,
            value=body.value,
            due_date=body.due_date,
            billing_type=body.billing_type,
            status=BillingStatus.PENDING,
            asaas_payment_id=payment.get("id"),
            asaas_subscription_id=payment.get("subscription"),
            checkout_url=AsaasClient.payment_checkout_url(payment),
            external_reference=external_reference,
        )
        await record_audit(
            db, action="create", entity="billing_charge",
            entity_id=charge.id, user_id=user.id, tenant_id=company.id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return BillingChargeRead.model_validate(charge)


@router.post("/subscriptions", response_model=BillingChargeRead, status_code=201)
async def create_subscription(
    body: BillingSubscriptionCreate,
    company_id: UUID | None = None,
    company_cnpj: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(BILLING_MANAGE)),
) -> BillingChargeRead:
    """Super Admin: cria ASSINATURA MENSAL (mensalidade recorrente).

    - `body.value` informado → usa esse valor e persiste como `monthly_fee`.
    - `body.value` ausente → usa o `monthly_fee` atual da empresa.
    - Impede assinatura DUPLICADA: se a empresa já tem uma ativa, recusa.
    - O `checkout_url` e o `asaas_payment_id` vêm do PRIMEIRO payment gerado
      pela assinatura (o objeto de assinatura NÃO tem `invoiceUrl`).
    """
    company = await _resolve_company(db, company_id, company_cnpj)

    # Previne assinatura duplicada no Asaas.
    repo = BillingRepository(db)
    active_sub = await repo.get_active_subscription(company.id)
    if active_sub:
        raise ValidationFailedError(
            "A empresa já possui uma assinatura ativa. "
            "Para alterar o valor, edite a assinatura existente."
        )

    monthly_value = body.value or company.monthly_fee
    if not monthly_value or monthly_value <= 0:
        raise ValidationFailedError(
            "Informe o valor da mensalidade ou cadastre o monthly_fee da empresa."
        )
    # Se o valor foi informado na criação, passa a valer como mensalidade padrão.
    if body.value is not None:
        company.monthly_fee = body.value
        await db.flush()

    next_due = body.next_due_date or (date.today() + timedelta(days=30))
    try:
        client = AsaasClient()
        customer_id = company.asaas_customer_id
        if not customer_id:
            customer_id = await client.get_or_create_customer(
                name=company.name, cpf_cnpj=company.cnpj
            )
            company.asaas_customer_id = customer_id
            await db.flush()
        external_reference = f"billing:{company.id}:mensalidade"
        sub = await client.create_subscription(
            customer_id=customer_id,
            value=float(monthly_value),
            next_due_date=next_due.isoformat(),
            billing_type=body.billing_type.value.upper(),
            description=f"Mensalidade — {company.name}",
            external_reference=external_reference,
        )
    except AsaasError as exc:
        raise ValidationFailedError(str(exc)) from exc

    sub_id = sub.get("id")

    # O checkout_url e o payment_id vêm do PRIMEIRO payment da assinatura.
    payment_id = None
    checkout_url = None
    try:
        payments = await client.list_subscription_payments(sub_id, limit=1)
        if payments:
            first = payments[0]
            payment_id = first.get("id")
            checkout_url = AsaasClient.payment_checkout_url(first)
    except AsaasError:
        # Sem URL ainda — o frontend pode buscá-la depois via /charges/{id}/pay.
        pass

    try:
        charge = await repo.create(
            tenant_id=company.id,
            type="mensalidade",
            value=monthly_value,
            due_date=next_due,
            billing_type=body.billing_type,
            status=BillingStatus.PENDING,
            asaas_payment_id=payment_id,
            asaas_subscription_id=sub_id,
            checkout_url=checkout_url,
            external_reference=external_reference,
        )
        await record_audit(
            db, action="create", entity="billing_subscription",
            entity_id=charge.id, user_id=user.id, tenant_id=company.id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return BillingChargeRead.model_validate(charge)


@router.get("/charges/{charge_id}/pay")
async def get_payment_url(
    charge_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Admin da empresa: obtém a URL de pagamento hospedada do Asaas.
    - Só o admin do tenant que é dono da cobrança acessa (isolamento).
    - Retorna a `invoiceUrl` (Fatura Asaas) para redirecionamento.
    """
    if not _is_company_admin(user):
        raise ValidationFailedError("Acesso negado.")
    repo = BillingRepository(db)
    charge = await repo.get(charge_id)
    if not charge:
        raise NotFoundError("Cobrança não encontrada.")
    if not charge.checkout_url and charge.asaas_payment_id:
        # Tenta buscar a URL atualizada no Asaas (cobrança pode ter sido recriada).
        try:
            client = AsaasClient()
            payment = await client.get_payment(charge.asaas_payment_id)
            charge.checkout_url = AsaasClient.payment_checkout_url(payment)
            await db.commit()
        except AsaasError:
            pass
    if not charge.checkout_url:
        raise ValidationFailedError("Não foi possível obter a página de pagamento.")
    return {"checkout_url": charge.checkout_url}