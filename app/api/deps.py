"""Dependencies de autenticação e autorização (seções 13 e 14 do doc).

- get_current_user: extrai o usuário do access token (cookie ou header).
- require_permission: verifica a permissão do usuário (RBAC).
- require_integration_key: autentica o AGENTE de integração por chave de API
  (integração NÃO nativa — Bloco I1). O tenant vem da chave, nunca do payload.
- super_admin tem acesso a tudo.
"""
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cookies import ACCESS_COOKIE
from app.core.context import TenantContext
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import SUPER_ADMIN
from app.core.tokens import TokenError, decode_token, ACCESS_TYPE
from app.database.session import get_db
from app.models import ERPIntegration, User
from app.repositories.integration import IntegrationRepository
from app.repositories.user import UserRepository

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extrai e valida o usuário autenticado a partir do access token."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        # Suporte a Authorization: Bearer (para testes/APIs)
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise UnauthorizedError("Não autenticado.")
    try:
        payload = decode_token(token, ACCESS_TYPE)
    except TokenError:
        raise UnauthorizedError("Não autenticado.")
    users = UserRepository(db)
    user = await users.get(payload["sub"])
    if user.status.value != "active":
        raise UnauthorizedError("Não autenticado.")
    # Popula o TenantContext com o tenant do usuário autenticado (seção 5)
    # Garante isolamento multi-tenant em todas as queries do request.
    TenantContext.set(
        tenant_id=user.tenant_id,
        user_id=user.id,
        is_super_admin=user.is_super_admin,
    )
    return user

def _user_permissions(user: User) -> set[str]:
    """Conjunto de permissões do usuário (via roles)."""
    if user.is_super_admin:
        return {SUPER_ADMIN}
    perms: set[str] = set()
    for role in user.roles:
        for p in role.permissions:
            perms.add(p.code)
    return perms

def require_permission(permission: str):
    """Factory de dependency: exige a permissão para acessar o endpoint."""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if user.is_super_admin:
            return user
        if permission not in _user_permissions(user):
            raise ForbiddenError("Acesso negado.")
        return user

    return _checker

# ---------- BLOCO I1 — autenticação do AGENTE de integração ----------
def _extract_api_key(request: Request) -> str | None:
    """Lê a chave do agente em `X-API-Key` (ou `Authorization: Api-Key <chave>`)."""
    raw = request.headers.get("x-api-key")
    if raw and raw.strip():
        return raw.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("api-key "):
        return auth[8:].strip()
    return None

async def require_integration_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ERPIntegration:
    """Autentica o agente de integração do cliente via chave de API.

    Substitui a sessão de usuário: o agente não faz login.
    - Resolve a integração pela chave (hash) — 401 se ausente/ inválida/inativa.
    - Popula o TenantContext com o tenant DA CHAVE (isolamento multi-tenant):
      o payload do agente nunca escolhe o tenant.
    """
    raw = _extract_api_key(request)
    if not raw:
        raise UnauthorizedError("Chave de API ausente.")

    repo = IntegrationRepository(db)
    integration = await repo.get_by_agent_api_key(raw)
    if (
        integration is None
        or not integration.is_active
        or integration.type != "agent"
    ):
        # Mensagem genérica (não revela se a chave existe ou está inativa).
        raise UnauthorizedError("Chave de API inválida.")

    TenantContext.set(
        tenant_id=integration.tenant_id,
        user_id=None,
        is_super_admin=False,
    )
    return integration
