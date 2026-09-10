"""Schemas do catálogo (seções 16 e 17 do doc)."""
import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------- Validação anti-XSS (descrição é conteúdo rico permitido, nunca scripts) ----------
_XSS_PATTERNS = (r"<\s*script", r"\bon\w+\s*=", r"javascript\s*:")

def _reject_xss(v: str | None) -> str | None:
    """Rejeita descrições com padrões XSS óbvios (defesa em profundidade)."""
    if v is None:
        return v
    lower = v.lower()
    if any(re.search(p, lower) for p in _XSS_PATTERNS):
        raise ValueError("Descrição contém conteúdo não permitido.")
    return v

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

# ---------- Desconto por quantidade (Desconto Progressivo) ----------
class QuantityTierRead(BaseModel):
    """Faixa de desconto por quantidade exibida ao cliente (vitrine).

    - discount_type: "percent" (valor = %) | "fixed" (valor = R$ por unidade).
    - label: rótulo pronto para exibição (ex.: "5% off" | "R$ 2,50 off/un").
    """

    model_config = ConfigDict(from_attributes=True)
    min_quantity: int
    discount_type: str
    discount_value: Decimal
    label: str | None = None

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
    # ✅ Estoque é SEMPRE inteiro (unidades) — a API devolve 15, nunca "15.000".
    stock: int | None = Field(None, ge=0)
    # ✅ URL EXTERNA da imagem (CDN/storage do cliente). Vazio/None = usa R2.
    image_url: str | None = Field(None, max_length=2048)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        return _reject_xss(v)

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
    # ✅ Estoque inteiro (validação: rejeita 15.5 com 422 — só aceita 15)
    stock: int | None = Field(None, ge=0)
    # ✅ URL EXTERNA. Enviar "" (string vazia) LIMPA a imagem externa (→ NULL).
    image_url: str | None = Field(None, max_length=2048)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        return _reject_xss(v)

class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    created_at: datetime
    # URL FINAL de exibição: externa (coluna) quando houver, senão a pública
    # do R2 (tabela files) — resolvida pelo backend.
    image_url: str | None = None
    # ✅ URL EXTERNA CRUA (valor da coluna, sem fallback para o R2).
    # Usada pelo formulário de edição para não "pinçar" a URL do R2 no campo.
    image_url_external: str | None = None

    # Preço calculado para o cliente (seção 17) — preenchido quando a listagem
    # é feita para um cliente (vitrine). Fica null nas demais listagens.
    customer_price: Decimal | None = None
    final_price: Decimal | None = None
    price_source: str | None = None  # "customer" | "price_list" | "default"

    # ✅ Faixas de desconto por quantidade (Desconto Progressivo) — exibição na vitrine
    quantity_discounts: list[QuantityTierRead] = []

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

    E, quando `quantity` é informado, aplica o desconto por quantidade
    (Desconto Progressivo) — `final_price` já sai com o desconto.
    """

    product_id: UUID
    sku: str
    name: str
    base_price: Decimal
    customer_price: Decimal | None = None
    final_price: Decimal
    price_source: str  # "customer" | "price_list" | "default"

    # ✅ Desconto por quantidade (Desconto Progressivo)
    quantity: int | None = None  # quantidade usada no cálculo (se informada)
    quantity_discounts: list[QuantityTierRead] = []

# ---------- Preços por cliente (gestão completa - Bloco A) ----------
class CustomerPriceUpdate(BaseModel):
    """Atualização do valor do preço especial (sem trocar cliente/produto)."""

    price: Decimal = Field(..., gt=0)  # preço deve ser positivo

class CustomerPriceDetailRead(BaseModel):
    """Preço especial enriquecido com nomes (cliente e produto) para a tela."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    customer_id: UUID
    customer_name: str | None = None
    product_id: UUID
    product_name: str | None = None
    product_sku: str | None = None
    price: Decimal

class CustomerPricePage(BaseModel):
    """Resposta paginada de preços especiais (padrão do schema de dados)."""

    items: list[CustomerPriceDetailRead]
    total: int
    page: int
    page_size: int
    pages: int

# ---------- Importação em massa de preços especiais (Bloco B3) ----------
class CustomerPriceImportResult(BaseModel):
    """Relatório da importação em massa de preços especiais.

    - created: quantos preços foram criados.
    - updated: quantos preços existentes foram atualizados (par duplicado).
    - skipped: linhas ignoradas.
    - errors: detalhe de cada linha com problema (row, error).
    """

    created: int
    updated: int
    skipped: int
    errors: list[dict]