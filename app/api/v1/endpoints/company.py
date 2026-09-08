"""Endpoints de empresas.

GET  /companies                 — Super Admin lista todas as empresas (paginado).
GET  /companies/branding        — identidade visual do tenant do usuário logado.
GET  /companies/by-domain/{d}   — público: resolve o tenant pelo domínio (pré-login).
POST /companies                 — Super Admin cria empresa (tenant) + convida o admin.
PATCH /companies/{id}/status    — Super Admin inativa/reativa a empresa em cascata
e notifica o admin por e-mail (NYD B2B).
PATCH /companies/{id}           — Super Admin atualiza branding (logo, favicon, cores, nome).

Usa o TenantContext (sessão autenticada) — nunca confia em domínio/ID vindo do front.
Rate limit do by-domain via Redis (Bloco 17) — funciona com múltiplas instâncias.
E-mail de convite: enviado via BackgroundTasks (o worker ARQ não roda no Render).
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_permission
from app.core.config import get_settings
from app.core.context import TenantContext
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    ValidationFailedError,
)
from app.core.invitations import compute_expires_at, generate_invite_token
from app.core.permissions import COMPANY_MANAGE
from app.core.rate_limit import check_rate_limit
from app.core.tokens import revoke_all_sessions
from app.database.session import get_db
from app.models import Permission, User
from app.models.catalog import Catalog, Category, PriceList, Product
from app.models.company import Company
from app.models.customer import Customer
from app.models.enums import (
    CompanyStatus,
    CustomerStatus,
    OrderStatus,
    ProductStatus,
    UserStatus,
)
from app.models.invitation import Invitation, InvitationStatus
from app.models.order import Order
from app.models.rbac import Role, role_permissions
from app.repositories.company import CompanyRepository
from app.repositories.invitation import InvitationRepository
from app.schemas.company import (
    CompanyBranding,
    CompanyPage,
    CompanyRead,
    CompanyStatusUpdate,
    CompanyUpdate,
)
from app.schemas.invitation import CompanyCreateRequest
from app.services.audit import record_audit
from app.services.email import send_company_status_email, send_invite_email
from app.services.rbac import ROLE_DEFINITIONS

router = APIRouter(prefix="/companies", tags=["Companies"])

# Limites do by-domain (anti-enumeração de tenants)
_DOMAIN_RATE_LIMIT = 30   # requisições por janela
_DOMAIN_WINDOW = 60       # segundos

async def _check_domain_rate_limit(domain: str) -> None:
    """Rate limit por domínio via Redis (anti-enumeração, multi-instância)."""
    allowed, _ = await check_rate_limit(
        f"domain:{domain.lower()}", _DOMAIN_RATE_LIMIT, _DOMAIN_WINDOW
    )
    if not allowed:
        raise RateLimitedError("Muitas tentativas. Tente novamente em instantes.")

@router.get("", response_model=CompanyPage)
async def list_companies(
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CompanyPage:
    """Lista todas as empresas (exclusivo Super Admin)."""
    if not user.is_super_admin:
        raise ForbiddenError("Apenas o Super Admin pode listar empresas.")

    repo = CompanyRepository(db)
    items, total = await repo.list_all(search, page, page_size)
    pages = (total + page_size - 1) // page_size
    return CompanyPage(
        items=items, total=total, page=page, page_size=page_size, pages=pages
    )

@router.get("/by-domain/{domain}", response_model=CompanyBranding)
async def get_company_by_domain(
    domain: str,
    db: AsyncSession = Depends(get_db),
) -> CompanyBranding:
    """Público: resolve o tenant (branding) pelo domínio customizado."""
    await _check_domain_rate_limit(domain)
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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(COMPANY_MANAGE)),
) -> dict:
    """Super Admin cria a empresa (tenant) + convida o admin da empresa.

    Também cria as roles padrão do tenant (RBAC).
    E-mail de convite enviado em background (não trava a resposta).
    """
    repo = CompanyRepository(db)
    # 1) Verifica duplicidade de slug/domínio/cnpj
    existing = await repo.find_by_slug_or_domain_or_cnpj(
        body.slug, body.domain, body.cnpj
    )
    if existing:
        raise ValidationFailedError(
            "Já existe uma empresa com este slug, domínio ou CNPJ."
        )

    # 2) Cria a empresa
    company = await repo.create_with_tenant(
        name=body.name,
        cnpj=body.cnpj,
        slug=body.slug,
        domain=body.domain,
        primary_color=body.primary_color,
        secondary_color=body.secondary_color,
        logo_url=body.logo_url,
        favicon_url=body.favicon_url,
        admin_email=body.admin_email,
        admin_full_name=body.admin_full_name,
    )
    # 3) Cria as roles padrão do tenant
    await db.execute(
        Role.__table__.insert(),
        [
            {
                "tenant_id": company.id,
                "name": definition["name"],
                "slug": definition["slug"],
                "description": definition.get("description"),
                "is_system": definition.get("is_system", True),
            }
            for definition in ROLE_DEFINITIONS.values()
        ],
    )
    # 4) Vincula as permissões das roles padrão
    roles = (
        await db.execute(
            select(Role).where(Role.tenant_id == company.id)
        )
    ).scalars().all()
    for role in roles:
        definition = ROLE_DEFINITIONS.get(role.slug)
        if not definition:
            continue
        perms = (
            await db.execute(
                select(Permission).where(
                    Permission.code.in_(definition["permissions"])
                )
            )
        ).scalars().all()
        for perm in perms:
            await db.execute(
                role_permissions.insert().values(
                    role_id=role.id, permission_id=perm.id
                )
            )

    # 5) Cria o usuário admin (status inactive até aceitar o convite)
    admin = User(
        tenant_id=company.id,
        email=body.admin_email,
        full_name=body.admin_full_name,
        status=UserStatus.INACTIVE,
    )
    db.add(admin)
    await db.flush()
    await db.execute(
        user_roles.insert().values(
            user_id=admin.id, role_id=next(r.id for r in roles if r.slug == "admin")
        )
    )

    # 6) Convite + e-mail em background
    token = generate_invite_token()
    expires_at = compute_expires_at()
    db.add(
        Invitation(
            tenant_id=company.id,
            email=body.admin_email,
            full_name=body.admin_full_name,
            role_slug="admin",
            token=token,
            expires_at=expires_at,
            status=InvitationStatus.PENDING,
        )
    )
    await record_audit(
        db, action="create", entity="company",
        entity_id=company.id, user_id=user.id, tenant_id=company.id,
    )
    await db.commit()

    background_tasks.add_task(
        send_invite_email,
        company.id,
        body.admin_email,
        body.admin_full_name,
        token,
        expires_at,
        body.admin_full_name,
    )
    return {"id": company.id, "name": company.name, "slug": company.slug, "status": "active"}

@router.patch("/{company_id}/status", response_model=CompanyRead)
async def update_company_status(
    company_id: UUID,
    body: CompanyStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(COMPANY_MANAGE)),
) -> CompanyRead:
    """Super Admin inativa/reativa a empresa em cascata"""
    if not user.is_super_admin:
        raise ForbiddenError("Apenas o Super Admin pode alterar empresas.")

    repo = CompanyRepository(db)
    company = await repo.get(company_id)
    if not company:
        raise NotFoundError("Empresa não encontrada.")

    new_status = body.status
    company.status = CompanyStatus(new_status)
    now = datetime.now(timezone.utc)

    if new_status == "inactive":
        # Usuários do tenant → inativos + sessões revogadas
        users = (
            await db.execute(select(User).where(User.tenant_id == company_id))
        ).scalars().all()
        for u in users:
            u.status = UserStatus.INACTIVE
        await revoke_all_sessions(company_id)

    await record_audit(
        db, action="update", entity="company",
        entity_id=company.id, user_id=user.id, tenant_id=company_id,
    )
    await db.commit()
    return CompanyRead.model_validate(company)

@router.patch("/{company_id}", response_model=CompanyRead)
async def update_company_branding(
    company_id: UUID,
    body: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(COMPANY_MANAGE)),
) -> CompanyRead:
    """Super Admin atualiza branding da empresa (logo, favicon, cores, nome)."""
    if not user.is_super_admin:
        raise ForbiddenError("Apenas o Super Admin pode alterar empresas.")

    repo = CompanyRepository(db)
    company = await repo.get(company_id)
    if not company:
        raise NotFoundError("Empresa não encontrada.")

    data = body.model_dump(exclude_unset=True, exclude_none=True)
    if "slug" in data:
        dup = await repo.find_by_slug_or_domain_or_cnpj(data["slug"], None, None)
        if dup and str(dup.id) != str(company_id):
            raise ValidationFailedError("Já existe uma empresa com este slug.")
    for key, value in data.items():
        setattr(company, key, value)

    await record_audit(
        db, action="update", entity="company",
        entity_id=company.id, user_id=user.id, tenant_id=company_id,
    )
    await db.commit()
    return CompanyRead.model_validate(company)