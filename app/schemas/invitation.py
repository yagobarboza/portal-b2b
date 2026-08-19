"""Schemas de convites (criação de usuários e empresas)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

class CompanyCreateRequest(BaseModel):
    """Criação de empresa pelo Super Admin + convite do admin."""
    name: str = Field(..., min_length=2, max_length=255)
    cnpj: str = Field(..., min_length=14, max_length=18)
    slug: str = Field(..., min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    domain: str | None = Field(None, max_length=255)
    primary_color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    secondary_color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    admin_email: EmailStr
    admin_full_name: str = Field(..., min_length=2, max_length=255)

class InviteCreate(BaseModel):
    """Convite de usuário para o tenant do usuário autenticado."""
    email: EmailStr
    full_name: str | None = Field(None, max_length=255)
    role_slug: str = Field(..., min_length=1, max_length=100)

class InviteResponse(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str | None = None
    role_slug: str
    status: str
    expires_at: datetime
    created_at: datetime

class InviteAccept(BaseModel):
    """Aceite do convite: o convidado define a própria senha."""
    token: str = Field(..., min_length=20)
    full_name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=8)