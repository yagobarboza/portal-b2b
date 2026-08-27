"""Repositório de Company (white-label — Fase 0)."""
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company

class CompanyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, company_id: UUID) -> Company | None:
        result = await self.db.execute(
            select(Company).where(Company.id == company_id)
        )
        return result.scalars().first()

    async def get_by_domain(self, domain: str) -> Company | None:
        """Busca uma empresa pelo domínio customizado (case-insensitive)."""
        stmt = select(Company).where(
            Company.domain == domain.lower()
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def list_all(
        self,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Company], int]:
        """Lista todas as empresas (Super Admin — visão da plataforma).

        Sem filtro por tenant: o Super Admin gerencia a plataforma inteira.
        Busca opcional por nome/CNPJ/slug + paginação.
        """
        base = select(Company)
        if search:
            like = f"%{search}%"
            base = base.where(
                or_(
                    Company.name.ilike(like),
                    Company.cnpj.ilike(like),
                    Company.slug.ilike(like),
                )
            )
        total = (
            await self.db.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(Company.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total