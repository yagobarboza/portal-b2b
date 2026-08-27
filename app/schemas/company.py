"""Schemas de Company/Branding (white-label Fase 0)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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
    created_at: datetime

class CompanyPage(BaseModel):
    items: list[CompanyRead]
    total: int
    page: int
    page_size: int
    pages: int