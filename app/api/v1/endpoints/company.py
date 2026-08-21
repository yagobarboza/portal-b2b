"""Endpoints de Company/Branding (white-label — Fase 0).
GET  /companies/branding        — identidade visual do tenant do usuário logado.
GET  /companies/by-domain/{d}   — público: resolve o tenant pelo domínio (pré-login).
POST /companies                 — Super Admin cria empresa (tenant) + convida o admin.
Usa o TenantContext (sessão autenticada) — nunca confia em domínio/ID vindo do front.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user, require_permission
from app.core.config import get_settings
from app.core.context import TenantContext
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationFailedError
from app.core.invitations import compute_expires_at, generate_invite_token
from app.core.permissions import COMPANY_MANAGE
from app.database.session import get_db
from app.models import User
from app.models.company import Company
from app.models.enums import CompanyStatus
from app.repositories.company import CompanyRepository
from app.repositories.invitation import InvitationRepository
from app.schemas.company import CompanyBranding
from app.schemas.invitation import CompanyCreateRequest
from app.services.audit import record_audit
from app.services.email import send_invite_email

router = APIRouter(prefix="/companies", tags=["Companies"])

def _client_ip(request: Request) -> str:
    """IP do cliente, respeitando proxy reverso (X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

@router.get("/by-domain/{domain}", response_model=CompanyBranding)
async def get_company_by_domain(
    domain: str,
    db: AsyncSession = Depends(get_db),
) -> CompanyBranding:
    """Público: resolve o tenant (branding) pelo domínio customizado.
    Usado pelo frontend ANTES do login para aplicar o white-label
    (ex.: b2b.fiobikeshop.com.br -> tenant da Fio Bikeshop).
    Sem autenticação — roda na resolução do tenant pelo host.
    """
    repo = CompanyRepository(db)
    company = await repo.get_by_domain(domain)
    if not company:
        raise NotFoundError("Empresa não encontrada para este domínio.")
    return CompanyBranding.model_validate(company)

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

@router.post("", status_code=201)
async def create_company_with_admin(
    body: CompanyCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(COMPANY_MANAGE)),
) -> dict:
    """Super Admin cria a empresa (tenant) + convida o admin da empresa.
    Fluxo:
    - Cria o Company (tenant) com branding inicial (cores/domínio opcionais).
    - Gera o convite e salva (commit) ANTES de tentar o envio de e-mail.
    - O envio de e-mail é não-bloqueante: se falhar, a empresa e o convite
      já foram persistidos e o erro vira apenas um log.
    - O admin convidado clica no link, define a senha e vira o admin do tenant.
    """
    if not user.is_super_admin:
        raise ForbiddenError("Apenas o Super Admin pode criar empresas.")

    company = Company(
        name=body.name,
        cnpj=body.cnpj,
        slug=body.slug,
        domain=body.domain,
        primary_color=body.primary_color,
        secondary_color=body.secondary_color,
        status=CompanyStatus.ACTIVE,
    )
    db.add(company)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValidationFailedError("CNPJ ou slug já cadastrado.")

    settings = get_settings()
    token = generate_invite_token()
    invitation = await InvitationRepository(db).create(
        email=body.admin_email,
        full_name=body.admin_full_name,
        role_slug=settings.DEFAULT_ADMIN_ROLE_SLUG,
        token=token,
        expires_at=compute_expires_at(),
        tenant_id=company.id,
        invited_by=user.id,
    )
    await record_audit(
        db,
        action="company_create",
        entity="company",
        entity_id=company.id,
        user_id=user.id,
        tenant_id=company.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    # Link do convite usa o domínio customizado da empresa (se houver),
    # com fallback para o FRONTEND_BASE_URL global.
    base_url = (company.domain or settings.FRONTEND_BASE_URL).rstrip("/")
    invite_url = f"{base_url}/accept-invite?token={token}"
    await send_invite_email(
        to_email=body.admin_email,
        invite_url=invite_url,
        company_name=company.name,
        expires_hours=settings.INVITE_TOKEN_EXPIRE_HOURS,
    )
    return {"status": "ok", "company_id": company.id, "invitation_id": invitation.id}