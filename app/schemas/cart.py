"""Schemas de carrinho (Bloco 7 — seção 21)."""
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class CartItemAdd(BaseModel):
    product_id: UUID
    # ✅ Quantidade é SEMPRE INTEIRA (unidades) — nunca "2.000".
    quantity: int = Field(gt=0)

class CartItemUpdate(BaseModel):
    # ✅ Quantidade inteira (rejeita 2.5 / 2.0001 com 422).
    quantity: int = Field(gt=0)

class CartItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID
    quantity: int  # ✅ API devolve 2, nunca "2.000"
    unit_price: Decimal
    subtotal: Decimal

class CartRead(BaseModel):
    id: UUID
    customer_id: UUID
    status: str
    items: list[CartItemRead]
    total: Decimal