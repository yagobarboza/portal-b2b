"""Repositório de Company (white-label — Fase 0)."""
from uuid import UUID

from sqlalchemy import select
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
        """Busca a empresa pelo domínio público (ex.: b2b.labianchi.com.br)."""
        result = await self.db.execute(
            select(Company).where(Company.domain == domain)
        )
        return result.scalars().first()