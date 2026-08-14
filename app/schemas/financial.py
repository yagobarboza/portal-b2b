"""Schemas do módulo financeiro (Bloco 10 — seção 27).

Somente leitura: dados sincronizados do ERP (princípio 5).
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import FinancialAccountStatus

class FinancialPaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    value: Decimal
    paid_at: datetime
    method: str | None

class FinancialAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    customer_id: UUID
    document: str
    value: Decimal
    due_date: datetime
    status: FinancialAccountStatus
    paid_at: datetime | None
    order_id: UUID | None
    external_id: str | None

class FinancialAccountDetailRead(FinancialAccountRead):
    days_overdue: int = 0
    payments: list[FinancialPaymentRead] = []

class FinancialAccountPage(BaseModel):
    items: list[FinancialAccountRead]
    total: int
    page: int
    page_size: int