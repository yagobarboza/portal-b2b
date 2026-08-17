"""Endpoints de Company/Branding (white-label — Fase 0).

GET /companies/branding — identidade visual do tenant do usuário logado.
Usa o TenantContext (sessão autenticada) — nunca confia em domínio/ID vindo do front.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.context import TenantContext
from app.core.exceptions import NotFoundError
from app.database.session import get_db
from app.models import User
from app.repositories.company import CompanyRepository
from app.schemas.company import CompanyBranding

router = APIRouter(prefix="/companies", tags=["Companies"])

@router.get("/branding", response_model=CompanyBranding)
async def get_branding(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CompanyBranding:
    """Identidade visual do tenant do usuário autenticado (white-label)."""
    tenant_id = TenantContext.tenant_id()
    if not tenant_id:
        raise NotFoundError("Tenant não identificado.")

    repo = CompanyRepository(db)
    company = await repo.get(tenant_id)
    if not company:
        raise NotFoundError("Empresa não encontrada.")

    return CompanyBranding.model_validate(company)