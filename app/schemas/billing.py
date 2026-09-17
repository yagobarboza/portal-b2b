"""Schemas de cobranças (BillingCharge)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import BillingStatus, BillingType

class BillingChargeCreate(BaseModel):
    """Criação de cobrança AVULSA (Super Admin) — implantação, custom ou módulo."""

    type: Literal["implantacao", "custom", "modulo"]
    value: Decimal = Field(..., gt=0, max_digits=14, decimal_places=2)
    due_date: date
    billing_type: BillingType = BillingType.PIX
    description: str | None = Field(default=None, max_length=200)

class BillingSubscriptionCreate(BaseModel):
    """Criação de ASSINATURA MENSAL (Super Admin).

    Se `value` for informado, é usado como valor da mensalidade (e passa a
    valer como `monthly_fee` da empresa). Se ausente, usa o `monthly_fee` atual.
    """

    value: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    billing_type: BillingType = BillingType.PIX
    next_due_date: date | None = None

class BillingChargeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    type: str
    value: Decimal
    due_date: date
    billing_type: BillingType
    status: BillingStatus
    checkout_url: str | None
    asaas_subscription_id: str | None
    paid_at: datetime | None
    created_at: datetime

class BillingChargePage(BaseModel):
    items: list[BillingChargeRead]
    total: int
    page: int
    page_size: int
    pages: int