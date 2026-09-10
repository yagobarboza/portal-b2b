"""Schemas de desconto por quantidade (Desconto Progressivo)."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class QuantityDiscountBase(BaseModel):
    product_id: UUID
    customer_id: UUID | None = None  # None = vale para TODOS os clientes
    min_quantity: int = Field(gt=0)
    discount_type: str = Field(pattern="^(percent|fixed)$")
    discount_value: Decimal = Field(gt=0)

class QuantityDiscountCreate(QuantityDiscountBase):
    pass

class QuantityDiscountUpdate(BaseModel):
    product_id: UUID | None = None
    customer_id: UUID | None = None
    min_quantity: int | None = Field(None, gt=0)
    discount_type: str | None = Field(None, pattern="^(percent|fixed)$")
    discount_value: Decimal | None = Field(None, gt=0)
    is_active: bool | None = None

class QuantityDiscountRead(BaseModel):
    """Regra enriquecida com nomes (produto e cliente) para a tela."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID
    product_name: str | None = None
    product_sku: str | None = None
    customer_id: UUID | None = None
    customer_name: str | None = None
    min_quantity: int
    discount_type: str
    discount_value: Decimal
    is_active: bool
    created_at: datetime

class QuantityDiscountPage(BaseModel):
    """Resposta paginada (padrão do schema de dados)."""
    items: list[QuantityDiscountRead]
    total: int
    page: int
    page_size: int
    pages: int

class QuantityDiscountImportResult(BaseModel):
    """Relatório da importação em massa via planilha.

    - created: regras novas criadas.
    - updated: regras existentes atualizadas (mesma chave produto+cliente+faixa).
    - skipped: linhas ignoradas.
    - errors: detalhe de cada linha com problema (row, error).
    """
    created: int
    updated: int
    skipped: int
    errors: list[dict]