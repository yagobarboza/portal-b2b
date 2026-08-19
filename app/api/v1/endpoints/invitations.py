"""Endpoints de convites: criação de usuários e empresas.

- POST   /invitations           -> admin da empresa convida usuário do tenant
- GET    /invitations           -> lista convites do tenant
- DELETE /invitations/{id}      -> cancela convite pendente
- POST   /invitations/accept    -> público: convidado define senha e vira usuário
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.config import get_settings
from app.core.exceptions import ForbiddenError, ValidationFailedError
from app.core.invitations import compute_expires_at, generate_invite_token
from app.core.permissions import USER_CREATE, USER_DELETE, USER_READ
from app.core.security import hash_password, validate_password_strength
from app.database.session import get_db
from app.models.company import Company
from app.models.enums import UserStatus
from app.models.invitation import Invitation, InvitationStatus
from app.models.rbac import Role, user_roles
from app.models.user import User
from app.repositories.invitation import InvitationRepository
from app.repositories.user import UserRepository
from app.schemas.invitation import InviteAccept, InviteCreate, InviteResponse
from app.services.audit import record_audit
from app.services.email import send_invite_email

router = APIRouter(tags=["invitations"])

async def _resolve_role(
    db: AsyncSession, role_slug: str, tenant_id: UUID | None
) -> Role | None:
    """Resolve a role pelo slug (global ou do tenant)."""
    stmt = select(Role).where(Role.slug == role_slug).where(
        (Role.tenant_id.is_(None)) | (Role.tenant_id == tenant_id)
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def _company_name(db: AsyncSession, tenant_id: UUID | None) -> str:
    if tenant_id is None:
        return "Portal B2B"
    company = await db.get(Company, tenant_id)
    return company.name if company else "Portal B2B"

async def _build_invite_url(token: str) -> str:
    settings = get_settings()
    return f"{settings.FRONTEND_BASE_URL}/aceitar-convite?token={token}"

@router.post("/invitations", response_model=InviteResponse, status_code=201)
async def create_invite(
    body: InviteCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(USER_CREATE)),
) -> InviteResponse:
    """Admin da empresa convida um usuário do próprio tenant."""
    if user.tenant_id is None and not user.is_super_admin:
        raise ForbiddenError("Usuário sem tenant vinculado.")

    tenant_id = user.tenant_id
    role = await _resolve_role(db, body.role_slug, tenant_id)
    if role is None:
        raise ValidationFailedError("Perfil (role) inválido para este convite.")

    if await UserRepository(db).get_by_email(body.email):
        raise ValidationFailedError("Este e-mail já possui cadastro.")

    repo = InvitationRepository(db)
    token = generate_invite_token()
    invitation = await repo.create(
        email=body.email,
        full_name=body.full_name,
        role_slug=body.role_slug,
        token=token,
        expires_at=compute_expires_at(),
        tenant_id=tenant_id,
        invited_by=user.id,
    )

    settings = get_settings()
    company_name = await _company_name(db, tenant_id)
    await send_invite_email(
        to_email=body.email,
        invite_url=await _build_invite_url(token),
        company_name=company_name,
        expires_hours=settings.INVITE_TOKEN_EXPIRE_HOURS,
    )

    await record_audit(
        db,
        action="invite_created",
        entity="invitation",
        entity_id=invitation.id,
        user_id=user.id,
        tenant_id=tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    return InviteResponse(
        id=invitation.id,
        email=invitation.email,
        full_name=invitation.full_name,
        role_slug=invitation.role_slug,
        status=invitation.status.value,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )

@router.get("/invitations", response_model=list[InviteResponse])
async def list_invites(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(USER_READ)),
) -> list[InviteResponse]:
    """Lista os convites do tenant (Super Admin vê todos)."""
    repo = InvitationRepository(db)
    invites = await repo.list_by_tenant(
        None if user.is_super_admin else user.tenant_id
    )
    return [
        InviteResponse(
            id=i.id,
            email=i.email,
            full_name=i.full_name,
            role_slug=i.role_slug,
            status=i.status.value,
            expires_at=i.expires_at,
            created_at=i.created_at,
        )
        for i in invites
    ]

@router.delete("/invitations/{invitation_id}", status_code=204)
async def cancel_invite(
    invitation_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(USER_DELETE)),
) -> None:
    """Cancela um convite pendente."""
    repo = InvitationRepository(db)
    invitation = await repo.get(invitation_id)
    if invitation.tenant_id != user.tenant_id and not user.is_super_admin:
        raise ForbiddenError("Você não pode cancelar este convite.")

    await repo.cancel(invitation)
    await record_audit(
        db,
        action="invite_cancelled",
        entity="invitation",
        entity_id=invitation.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

@router.post("/invitations/accept")
async def accept_invite(
    body: InviteAccept,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Público: o convidado define a própria senha e o usuário é criado."""
    validate_password_strength(body.password)

    repo = InvitationRepository(db)
    invitation = await repo.get_by_token(body.token)
    if (
        invitation is None
        or invitation.status != InvitationStatus.PENDING
        or invitation.expires_at < datetime.now(timezone.utc)
    ):
        raise ValidationFailedError("Convite inválido ou expirado.")

    if await UserRepository(db).get_by_email(invitation.email):
        raise ValidationFailedError("Este e-mail já possui cadastro.")

    role = await _resolve_role(db, invitation.role_slug, invitation.tenant_id)
    if role is None:
        raise ValidationFailedError("Perfil (role) do convite não existe mais.")

    user = User(
        email=invitation.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        tenant_id=invitation.tenant_id,
        is_super_admin=False,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    await db.flush()
    await db.execute(
        user_roles.insert().values(user_id=user.id, role_id=role.id)
    )
    await repo.mark_accepted(invitation)

    await record_audit(
        db,
        action="invite_accepted",
        entity="user",
        entity_id=user.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"status": "ok", "message": "Cadastro concluído. Faça login."}

def _client_ip(request: Request) -> str:
    """IP do cliente, respeitando proxy reverso (X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"