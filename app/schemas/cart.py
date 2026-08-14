"""Schemas de carrinho (Bloco 7 — seção 21)."""
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class CartItemAdd(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0)

class CartItemUpdate(BaseModel):
    quantity: Decimal = Field(gt=0)

class CartItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID
    quantity: Decimal
    unit_price: Decimal
    subtotal: Decimal

class CartRead(BaseModel):
    id: UUID
    customer_id: UUID
    status: str
    items: list[CartItemRead]
    total: Decimal