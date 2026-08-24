"""Endpoints de Roles customizadas do tenant (RBAC).
- GET/POST /roles                -> listar/criar roles do tenant
- GET/PATCH/DELETE /roles/{id}   -> detalhe/editar/excluir
- Apenas Admin da Empresa (admin:manage)
"""
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import ADMIN_MANAGE
from app.database.session import get_db
from app.models import User
from app.models.rbac import Permission, Role, role_permissions
from app.schemas.role import RoleCreate, RoleList, RoleRead, RoleUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/roles", tags=["Perfis"])

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

async def _get_permissions(
    db: AsyncSession, codes: list[str]
) -> list[Permission]:
    if not codes:
        return []
    result = await db.execute(
        select(Permission).where(Permission.code.in_(codes))
    )
    return list(result.scalars().all())

@router.get("", response_model=RoleList)
async def list_roles(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ADMIN_MANAGE)),
) -> RoleList:
    """Lista roles globais + roles do tenant."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    stmt = select(Role).where(
        (Role.tenant_id.is_(None)) | (Role.tenant_id == user.tenant_id)
    )
    result = await db.execute(stmt)
    roles = list(result.scalars().all())
    return RoleList(
        items=[
            RoleRead(
                id=r.id, name=r.name, slug=r.slug,
                description=r.description, is_system=r.is_system,
                permissions=[p.code for p in r.permissions],
            )
            for r in roles
        ]
    )

@router.post("", response_model=RoleRead, status_code=201)
async def create_role(
    body: RoleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ADMIN_MANAGE)),
) -> RoleRead:
    """Cria uma role customizada do tenant."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    if not user.tenant_id:
        raise ValidationError("Usuário sem tenant vinculado.")

    # Valida slug único no tenant
    existing = await db.execute(
        select(Role).where(Role.slug == body.slug, Role.tenant_id == user.tenant_id)
    )
    if existing.scalars().first():
        raise ValidationError("Já existe um perfil com este slug.")

    permissions = await _get_permissions(db, body.permission_codes)
    role = Role(
        tenant_id=user.tenant_id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        is_system=False,
        permissions=permissions,
    )
    db.add(role)
    await record_audit(
        db, action="create", entity="role",
        entity_id=role.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return RoleRead(
        id=role.id, name=role.name, slug=role.slug,
        description=role.description, is_system=role.is_system,
        permissions=[p.code for p in role.permissions],
    )

@router.patch("/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: UUID,
    body: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ADMIN_MANAGE)),
) -> RoleRead:
    """Edita uma role customizada do tenant (não edita roles de sistema)."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    result = await db.execute(
        select(Role).where(Role.id == role_id, Role.tenant_id == user.tenant_id)
    )
    role = result.scalars().first()
    if not role:
        raise NotFoundError("Perfil não encontrado.")
    if role.is_system:
        raise ValidationError("Perfis de sistema não podem ser editados.")

    data = body.model_dump(exclude_unset=True)
    if "permission_codes" in data:
        permissions = await _get_permissions(db, data.pop("permission_codes"))
        role.permissions = permissions
    for key, value in data.items():
        if value is not None:
            setattr(role, key, value)
    await record_audit(
        db, action="update", entity="role",
        entity_id=role.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return RoleRead(
        id=role.id, name=role.name, slug=role.slug,
        description=role.description, is_system=role.is_system,
        permissions=[p.code for p in role.permissions],
    )

@router.delete("/{role_id}", status_code=204)
async def delete_role(
    role_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ADMIN_MANAGE)),
) -> None:
    """Exclui uma role customizada do tenant."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    result = await db.execute(
        select(Role).where(Role.id == role_id, Role.tenant_id == user.tenant_id)
    )
    role = result.scalars().first()
    if not role:
        raise NotFoundError("Perfil não encontrado.")
    if role.is_system:
        raise ValidationError("Perfis de sistema não podem ser excluídos.")
    await db.execute(role_permissions.delete().where(role_permissions.c.role_id == role.id))
    await db.delete(role)
    await record_audit(
        db, action="delete", entity="role",
        entity_id=role.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()