"""Schemas de autenticação (seções 8, 11 e 12 do doc)."""
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)

class RefreshRequest(BaseModel):
    refresh_token: str | None = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class UserInfo(BaseModel):
    """Usuário autenticado (GET /auth/me).

    `roles` e `permissions` alimentam o frontend para renderizar
    menus e controles por perfil (RBAC — seção 13). O backend
    SEMPRE revalida a permissão no endpoint (nunca confia no front).
    """
    id: UUID
    email: EmailStr
    full_name: str
    tenant_id: UUID | None = None
    is_super_admin: bool = False
    mfa_enabled: bool = False
    customer_id: UUID | None = None
    roles: list[str] = []          # slugs das roles do usuário
    permissions: list[str] = []    # códigos de permissão efetivos

class MfaSetupResponse(BaseModel):
    secret: str
    qr_code: str
    recovery_codes: list[str]

class MfaVerifyRequest(BaseModel):
    secret: str
    code: str

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)