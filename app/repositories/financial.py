"""Repositório financeiro (Bloco 10 — seção 27).

- Somente leitura: dados sincronizados do ERP via integração (princípio 5).
- Contas em aberto, pagas e vencidas (com dias de atraso).
- TODAS as queries filtram por tenant_id (isolamento, seção 5).
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.models import FinancialAccount, FinancialPayment
from app.models.enums import FinancialAccountStatus

class FinancialRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def list_accounts(
        self,
        customer_id: UUID | None,
        status: FinancialAccountStatus,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[FinancialAccount], int]:
        """Lista contas por status. Se customer_id, filtra por cliente."""
        base = select(FinancialAccount).where(
            FinancialAccount.tenant_id == self._tenant(),
            FinancialAccount.status == status,
        )
        if customer_id:
            base = base.where(FinancialAccount.customer_id == customer_id)
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(FinancialAccount.due_date.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get(self, account_id: UUID) -> FinancialAccount | None:
        result = await self.db.execute(
            select(FinancialAccount).where(
                FinancialAccount.id == account_id,
                FinancialAccount.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list_payments(self, account_id: UUID) -> list[FinancialPayment]:
        result = await self.db.execute(
            select(FinancialPayment)
            .where(
                FinancialPayment.account_id == account_id,
                FinancialPayment.tenant_id == self._tenant(),
            )
            .order_by(FinancialPayment.paid_at.asc())
        )
        return list(result.scalars().all())