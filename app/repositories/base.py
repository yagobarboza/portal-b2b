from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database.base import Base

class BaseRepository:
    """Repositório base com isolamento de tenant OBRIGATÓRIO.

    Toda operação aplica automaticamente o filtro tenant_id do
    contexto corrente. O desenvolvedor não precisa — e não deve —
    lembrar do filtro: ele é aplicado aqui, centralmente (seção 5).
    """

    model: type[Base]
    tenant_scoped: bool = True

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- helpers internos ----------

    def _has_attr(self, name: str) -> bool:
        return hasattr(self.model, name)

    def _apply_tenant(self, stmt):
        """Aplica o filtro de tenant. Sem contexto e sem super admin → 403."""
        if not self.tenant_scoped or not self._has_attr("tenant_id"):
            return stmt
        tenant_id = TenantContext.tenant_id()
        if tenant_id is None and not TenantContext.is_super_admin():
            raise ForbiddenError("Contexto de tenant ausente.")
        if tenant_id is not None:
            stmt = stmt.where(self.model.tenant_id == tenant_id)
        return stmt

    def _apply_soft_delete(self, stmt):
        if self._has_attr("is_deleted"):
            stmt = stmt.where(self.model.is_deleted == False)  # noqa: E712
        return stmt

    # ---------- leitura ----------

    async def get(self, id: UUID):
        stmt = select(self.model).where(self.model.id == id)
        stmt = self._apply_tenant(stmt)
        stmt = self._apply_soft_delete(stmt)
        result = await self.session.execute(stmt)
        obj = result.scalar_one_or_none()
        if obj is None:
            raise NotFoundError()
        return obj

    async def get_or_none(self, id: UUID):
        try:
            return await self.get(id)
        except NotFoundError:
            return None

    async def list(self, *, limit: int = 50, offset: int = 0, **filters) -> Sequence:
        stmt = select(self.model)
        stmt = self._apply_tenant(stmt)
        stmt = self._apply_soft_delete(stmt)
        for key, value in filters.items():
            if self._has_attr(key) and value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        stmt = stmt.order_by(self.model.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self, **filters) -> int:
        stmt = select(func.count()).select_from(self.model)
        stmt = self._apply_tenant(stmt)
        stmt = self._apply_soft_delete(stmt)
        for key, value in filters.items():
            if self._has_attr(key) and value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    # ---------- escrita ----------

    async def create(self, **values):
        if self.tenant_scoped and self._has_attr("tenant_id"):
            tenant_id = TenantContext.tenant_id()
            if tenant_id is None and not TenantContext.is_super_admin():
                raise ForbiddenError("Contexto de tenant ausente.")
            if tenant_id is not None and "tenant_id" not in values:
                values["tenant_id"] = tenant_id
        obj = self.model(**values)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, id: UUID, **values):
        obj = await self.get(id)
        for key, value in values.items():
            if hasattr(obj, key):
                setattr(obj, key, value)
        await self.session.flush()
        return obj

    async def delete(self, id: UUID) -> None:
        obj = await self.get(id)
        if self._has_attr("is_deleted"):
            obj.is_deleted = True
            obj.deleted_at = datetime.now(timezone.utc)
        else:
            await self.session.delete(obj)
        await self.session.flush()