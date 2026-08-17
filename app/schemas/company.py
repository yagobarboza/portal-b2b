"""Schemas de Company/Branding (white-label Fase 0)."""
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