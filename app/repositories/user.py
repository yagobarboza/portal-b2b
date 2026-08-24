"""Repositório de usuários (autenticação + gestão de equipe)."""
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.exceptions import NotFoundError
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
        base = select(User).where(User.tenant_id == tenant_id)
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

    async def update_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await self.session.flush()

    async def update(self, user: User, data: dict) -> User:
        for key, value in data.items():
            if value is not None:
                setattr(user, key, value)
        await self.session.flush()
        return user