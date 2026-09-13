"""Repositório de usuários (autenticação + gestão de equipe).

✅ A listagem de equipe (GET /users) agora EXCLUI clientes: apenas
usuários de equipe do tenant (customer_id IS NULL) são retornados.
Clientes aparecem SOMENTE no módulo de Clientes (GET /customers).
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.core.mfa import hash_recovery_code
from app.models import User

class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, user_id: UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFoundError("Usuário não encontrado.")
        return user

    async def get_by_tenant(self, user_id: UUID, tenant_id: UUID) -> User | None:
        result = await self.session.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(User.id == user_id, User.tenant_id == tenant_id)
        )
        return result.scalars().first()

    async def list_by_tenant(
        self, tenant_id: UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[User], int]:
        """Lista apenas EQUIPE do tenant (exclui contas de cliente)."""
        base = select(User).where(
            User.tenant_id == tenant_id,
            User.customer_id.is_(None),
        )
        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar() or 0
        result = await self.session.execute(
            base.options(selectinload(User.roles))
            .order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def set_mfa(self, user: User, secret: str, enabled: bool) -> None:
        user.mfa_secret_encrypted = secret if enabled else None
        user.mfa_enabled = enabled
        await self.session.flush()

    # ---------- Códigos de recuperação (MFA) ----------

    async def set_mfa_recovery_codes(self, user: User, hashed_codes: list[str]) -> None:
        """Substitui os códigos de recuperação (já com hash)."""
        user.mfa_recovery_codes_hashed = hashed_codes
        await self.session.flush()

    async def get_mfa_recovery_codes(self, user: User) -> list[str]:
        """Retorna os hashes dos códigos de recuperação."""
        return user.mfa_recovery_codes_hashed or []

    async def consume_mfa_recovery_code(self, user: User, code: str) -> bool:
        """Consome um código de recuperação (uso único) e revoga os demais.

        Segurança padrão: se um código de recuperação é usado, TODOS os
        outros são invalidados (evita reuso e limita a janela de exposição).
        Retorna True se o código era válido e foi consumido.
        """
        codes = list(user.mfa_recovery_codes_hashed or [])
        target = hash_recovery_code(code)
        if target not in codes:
            return False
        # Revoga todos os códigos restantes (uso único + invalidação total)
        user.mfa_recovery_codes_hashed = []
        await self.session.flush()
        return True

    async def clear_mfa_recovery_codes(self, user: User) -> None:
        """Limpa todos os códigos de recuperação (ex.: desativar MFA)."""
        user.mfa_recovery_codes_hashed = []
        await self.session.flush()

    async def update_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await self.session.flush()

    async def update(self, user: User, data: dict) -> User:
        for key, value in data.items():
            if value is not None:
                setattr(user, key, value)
        await self.session.flush()
        return user