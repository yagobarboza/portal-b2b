"""Repositório de Company (white-label — Fase 0)."""
import re
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.enums import CompanyStatus

class CompanyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, company_id: UUID) -> Company | None:
        result = await self.db.execute(
            select(Company).where(Company.id == company_id)
        )
        return result.scalars().first()

    async def get_by_cnpj(self, cnpj: str) -> Company | None:
        """Busca empresa por CNPJ, normalizando máscara (pontos/barras/hífen).

        Aceita '00.000.000/0000-00' ou '00000000000000' — a normalização é
        feita no SQL (empresas podem estar salvas com ou sem máscara).
        """
        digits = re.sub(r"\D", "", cnpj)
        if not digits:
            return None
        normalized = (
            func.replace(
                func.replace(
                    func.replace(
                        func.replace(Company.cnpj, ".", ""), "/", ""
                    ),
                    "-", "",
                ),
                " ", "",
            )
        )
        result = await self.db.execute(
            select(Company).where(normalized == digits)
        )
        return result.scalars().first()

    async def get_by_domain(self, domain: str) -> Company | None:
        """Busca uma empresa pelo domínio customizado (case-insensitive)."""
        stmt = select(Company).where(
            Company.domain == domain.lower()
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def set_status(
        self, company: Company, status: CompanyStatus
    ) -> Company:
        """Altera o status da empresa (ativa/inativa).

        Reutilizado pela reativação por pagamento (webhook Asaas) e pela
        inativação por não pagamento (job de 30 dias). O cascade de
        usuários/sessões é tratado no serviço que chama este método.
        """
        company.status = status
        await self.db.flush()
        return company

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