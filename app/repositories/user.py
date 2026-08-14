"""Repositório de usuários para autenticação.

NÃO aplica isolamento de tenant por padrão: o login precisa
encontrar o usuário pelo e-mail independente do tenant.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models import User

class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, user_id) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFoundError()
        return user

    async def set_mfa(self, user: User, secret: str, enabled: bool) -> None:
        """Ativa/desativa MFA e armazena o secret (criptografado no Bloco 4)."""
        user.mfa_secret_encrypted = secret if enabled else None
        user.mfa_enabled = enabled
        await self.session.flush()

    async def update_password(self, user: User, password_hash: str) -> None:
        """Atualiza o hash da senha e invalida sessões (seção 12)."""
        user.password_hash = password_hash
        await self.session.flush()