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

# ---------- MFA (seção 11) ----------
class MfaSetupResponse(BaseModel):
    secret: str
    qr_code: str
    recovery_codes: list[str]

class MfaVerifyRequest(BaseModel):
    secret: str
    code: str

# ✅ Desativação do MFA: exige confirmação com a senha atual OU um código
# TOTP válido (pelo menos um dos dois deve ser informado e validado).
class MfaDisableRequest(BaseModel):
    password: str | None = Field(None, min_length=1)
    code: str | None = Field(None, min_length=6, max_length=32)

# ✅ Segundo fator no LOGIN: devolvido quando a senha está correta mas o
# usuário tem MFA ativo. O frontend deve exibir o campo de código e chamar
# POST /auth/mfa/verify-login com este challenge_token.
class MfaChallengeResponse(BaseModel):
    mfa_required: bool = True
    challenge_token: str
    email: EmailStr

# ✅ Validação do segundo fator no login (código TOTP do app autenticador
# OU um código de recuperação).
class MfaLoginVerifyRequest(BaseModel):
    challenge_token: str
    code: str = Field(..., min_length=6, max_length=32)

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)