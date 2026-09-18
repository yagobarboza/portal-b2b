"""Schemas de Company/Branding (white-label Fase 0)."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompanyBranding(BaseModel):
    """Identidade visual do tenant (servida ao frontend)."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    domain: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None


class CompanyRead(BaseModel):
    """Visão de listagem de empresas (Super Admin)."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    cnpj: str | None = None
    slug: str
    domain: str | None = None
    status: str
    primary_color: str | None = None
    secondary_color: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    created_at: datetime


class CompanyPage(BaseModel):
    items: list[CompanyRead]
    total: int
    page: int
    page_size: int
    pages: int


class CompanyStatusUpdate(BaseModel):
    """Alteração de status da empresa (Super Admin)."""
    status: Literal["active", "inactive"]


class CompanyUpdate(BaseModel):
    """Atualização de branding/dados da empresa (Super Admin).

    - Logo e favicon: URLs públicas (R2 ou CDN) validadas no frontend.
    - Cores: hex (#RRGGBB) usadas pelo white-label.
    """
    name: str | None = None
    slug: str | None = None
    domain: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None


# ===== Regras de Compra (configuráveis pela própria empresa) =====
class CompanyPurchaseRules(BaseModel):
    """Regras de compra da empresa (lidas pela própria empresa / staff)."""
    min_order_value: Decimal | None = None
    min_order_quantity: int | None = None


class CompanyPurchaseRulesUpdate(BaseModel):
    """Atualização das regras de compra (staff).

    Use `exclude_unset=True` no endpoint: enviar o campo como `null` remove a
    regra; omitir mantém o valor atual.
    """
    min_order_value: Decimal | None = Field(default=None, ge=0)
    min_order_quantity: int | None = Field(default=None, ge=0)