"""Schemas de pedidos (Bloco 7 — seção 22)."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

class OrderCreate(BaseModel):
    notes: str | None = None

class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID
    quantity: Decimal
    unit_price: Decimal
    subtotal: Decimal

class OrderStatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    from_status: str | None
    to_status: str
    note: str | None
    created_at: datetime

class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: str
    customer_id: UUID
    status: str
    total: Decimal
    notes: str | None
    created_at: datetime
    items: list[OrderItemRead]
    status_history: list[OrderStatusHistoryRead]