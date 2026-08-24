"""Schemas de Pedidos (inclui aprovação/gestão do tenant)."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class OrderStatusUpdate(BaseModel):
    """Mudança de status de um pedido (apenas tenant)."""
    status: str = Field(..., description="Novo status do pedido")
    note: str | None = Field(None, max_length=500)

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
    from_status: str | None = None
    to_status: str
    note: str | None = None
    created_at: datetime

class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    number: str
    customer_id: UUID
    status: str
    total: Decimal
    notes: str | None = None
    created_at: datetime
    items: list[OrderItemRead] = []
    status_history: list[OrderStatusHistoryRead] = []

class OrderPage(BaseModel):
    items: list[OrderRead]
    total: int
    page: int
    page_size: int
    pages: int