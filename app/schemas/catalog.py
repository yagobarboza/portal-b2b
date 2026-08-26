"""Schemas do catálogo (seções 16 e 17 do doc)."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------- Category ----------
class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    slug: str = Field(..., min_length=1, max_length=150)
    parent_id: UUID | None = None

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    slug: str | None = Field(None, min_length=1, max_length=150)
    parent_id: UUID | None = None
    is_active: bool | None = None

class CategoryRead(CategoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    is_active: bool
    created_at: datetime

# ---------- Product ----------
class ProductBase(BaseModel):
    sku: str = Field(..., min_length=1, max_length=80)
    code: str | None = Field(None, max_length=80)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    brand: str | None = Field(None, max_length=100)
    category_id: UUID | None = None
    unit: str | None = Field(None, max_length=20)
    price: Decimal = Field(..., ge=0)  # preço padrão (seção 17)
    stock: Decimal | None = Field(None, ge=0)

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    sku: str | None = Field(None, min_length=1, max_length=80)
    code: str | None = Field(None, max_length=80)
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    brand: str | None = Field(None, max_length=100)
    category_id: UUID | None = None
    unit: str | None = Field(None, max_length=20)
    price: Decimal | None = Field(None, ge=0)
    stock: Decimal | None = Field(None, ge=0)

class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    created_at: datetime
    image_url: str | None = None  # Signed URL da imagem principal (R2) — resolvida pelo backend

# ---------- Paginação ----------
class ProductListParams(BaseModel):
    """Parâmetros de busca, filtro e ordenação de produtos.

    - search: busca por nome/SKU/código (ILIKE).
    - category_id: filtra por categoria.
    - status: filtra por status (active/inactive).
    - min_price / max_price: faixa de preço.
    - sort_by: coluna de ordenação (whitelist).
    - sort_dir: asc/desc.
    - page / page_size: paginação (com limites).
    """
    search: str | None = Field(None, max_length=255)
    category_id: UUID | None = None
    status: str | None = Field(None, pattern="^(active|inactive)$")
    min_price: Decimal | None = Field(None, ge=0)
    max_price: Decimal | None = Field(None, ge=0)
    sort_by: str = Field("created_at", pattern="^(name|sku|price|created_at|status)$")
    sort_dir: str = Field("desc", pattern="^(asc|desc)$")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)  # limite anti-DoS (seção 53)

class ProductPage(BaseModel):
    """Resposta paginada de produtos."""
    items: list[ProductRead]
    total: int
    page: int
    page_size: int
    pages: int

# ---------- Preços ----------
class PriceListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    description: str | None = None

class PriceListRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None
    is_active: bool

class CustomerPriceCreate(BaseModel):
    customer_id: UUID
    product_id: UUID
    price: Decimal = Field(..., gt=0)  # preço deve ser positivo

class CustomerPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    customer_id: UUID
    product_id: UUID
    price: Decimal

class PriceQuote(BaseModel):
    """Preço final calculado para um produto (seção 17).

    O backend aplica a prioridade:
    1. Preço específico do cliente (CustomerPrice)
    2. Preço da tabela (PriceList)
    3. Preço padrão do produto
    """
    product_id: UUID
    sku: str
    name: str
    base_price: Decimal
    customer_price: Decimal | None = None
    final_price: Decimal
    price_source: str  # "customer" | "price_list" | "default"