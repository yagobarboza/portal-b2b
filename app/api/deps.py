"""Dependencies de autenticação e autorização (seções 13 e 14 do doc).

- get_current_user: extrai o usuário do access token (cookie ou header).
- require_permission: verifica a permissão do usuário (RBAC).
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
from app.models import User
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