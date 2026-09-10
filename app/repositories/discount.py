"""Repositório de desconto por quantidade (Desconto Progressivo)."""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.models import QuantityDiscount

class QuantityDiscountRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> QuantityDiscount:
        obj = QuantityDiscount(tenant_id=self._tenant(), **data)
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get(self, discount_id: UUID) -> QuantityDiscount | None:
        """Busca uma regra (isolada por tenant — evita IDOR/BOLA)."""
        result = await self.db.execute(
            select(QuantityDiscount).where(
                QuantityDiscount.id == discount_id,
                QuantityDiscount.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def update(
        self, discount: QuantityDiscount, data: dict
    ) -> QuantityDiscount:
        for key, value in data.items():
            setattr(discount, key, value)
        await self.db.flush()
        return discount

    async def delete(self, discount: QuantityDiscount) -> None:
        await self.db.delete(discount)
        await self.db.flush()

    async def find_by_key(
        self,
        product_id: UUID,
        customer_id: UUID | None,
        min_quantity: int,
    ) -> QuantityDiscount | None:
        """Localiza regra existente pela CHAVE (produto + cliente + faixa).

        Usada no UPSERT da importação em massa: mesma chave = atualiza.
        Para customer_id NULL, usa IS NULL (senão o Postgres nunca acharia).
        """
        stmt = select(QuantityDiscount).where(
            QuantityDiscount.product_id == product_id,
            QuantityDiscount.min_quantity == min_quantity,
            QuantityDiscount.tenant_id == self._tenant(),
        )
        if customer_id is None:
            stmt = stmt.where(QuantityDiscount.customer_id.is_(None))
        else:
            stmt = stmt.where(QuantityDiscount.customer_id == customer_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def list_rules(
        self,
        product_id: UUID | None = None,
        customer_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[QuantityDiscount], int]:
        """Lista regras paginadas (filtros opcionais por produto/cliente)."""
        stmt = select(QuantityDiscount).where(
            QuantityDiscount.tenant_id == self._tenant()
        )
        count_stmt = (
            select(func.count())
            .select_from(QuantityDiscount)
            .where(QuantityDiscount.tenant_id == self._tenant())
        )
        if product_id:
            stmt = stmt.where(QuantityDiscount.product_id == product_id)
            count_stmt = count_stmt.where(
                QuantityDiscount.product_id == product_id
            )
        if customer_id:
            stmt = stmt.where(QuantityDiscount.customer_id == customer_id)
            count_stmt = count_stmt.where(
                QuantityDiscount.customer_id == customer_id
            )
        stmt = (
            stmt.order_by(
                QuantityDiscount.product_id,
                QuantityDiscount.min_quantity.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self.db.execute(stmt)).scalars().all()
        total = (await self.db.execute(count_stmt)).scalar_one()
        return list(items), total

    async def list_active_for_product(
        self, product_id: UUID
    ) -> list[QuantityDiscount]:
        """Regras ATIVAS de um produto (usado na precificação — Bloco 2)."""
        result = await self.db.execute(
            select(QuantityDiscount)
            .where(
                QuantityDiscount.product_id == product_id,
                QuantityDiscount.is_active.is_(True),
                QuantityDiscount.tenant_id == self._tenant(),
            )
            .order_by(QuantityDiscount.min_quantity.asc())
        )
        return list(result.scalars().all())

    async def list_active_global_for_product(
        self, product_id: UUID
    ) -> list[QuantityDiscount]:
        """Regras ATIVAS e GLOBAIS (customer_id IS NULL) de um produto.

        Usado na precificação como fallback para TODOS os clientes.
        """
        result = await self.db.execute(
            select(QuantityDiscount)
            .where(
                QuantityDiscount.product_id == product_id,
                QuantityDiscount.customer_id.is_(None),
                QuantityDiscount.is_active.is_(True),
                QuantityDiscount.tenant_id == self._tenant(),
            )
            .order_by(QuantityDiscount.min_quantity.asc())
        )
        return list(result.scalars().all())

    async def list_active_for_customer_product(
        self, customer_id: UUID, product_id: UUID
    ) -> list[QuantityDiscount]:
        """Regras ATIVAS e ESPECÍFICAS de um cliente para um produto.

        Usado na precificação (regra específica vence a global).
        """
        result = await self.db.execute(
            select(QuantityDiscount)
            .where(
                QuantityDiscount.customer_id == customer_id,
                QuantityDiscount.product_id == product_id,
                QuantityDiscount.is_active.is_(True),
                QuantityDiscount.tenant_id == self._tenant(),
            )
            .order_by(QuantityDiscount.min_quantity.asc())
        )
        return list(result.scalars().all())