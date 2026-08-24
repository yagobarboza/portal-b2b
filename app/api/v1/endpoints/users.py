"""Endpoints de Usuários/Equipe do tenant (gestão).
- GET    /users           -> listar usuários do tenant (users:read)
- PATCH  /users/{id}      -> editar nome/telefone/status/roles (users:update)
- DELETE /users/{id}      -> desativar usuário (users:delete)
- CRIAÇÃO de usuário: SEMPRE via POST /invitations (fluxo de convite),
  que restringe a role ao tenant e envia e-mail com link de aceite.
- Isolamento por tenant em todas as operações.
"""
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import USER_DELETE, USER_READ, USER_UPDATE
from app.database.session import get_db
from app.models import User
from app.models.enums import UserStatus
from app.models.rbac import Role, user_roles
from app.repositories.user import UserRepository
from app.schemas.user import UserPage, UserRead, UserUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["Usuários"])

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

async def _resolve_role(
    db: AsyncSession, role_slug: str, tenant_id: UUID | None
) -> Role | None:
    """Resolve a role RESTRITA ao tenant (evita privilege escalation)."""
    result = await db.execute(
        select(Role).where(
            Role.slug == role_slug,
            Role.tenant_id == tenant_id,
        )
    )
    return result.scalars().first()

@router.get("", response_model=UserPage)
async def list_users(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(USER_READ)),
) -> UserPage:
    """Lista usuários do tenant (apenas empresa)."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = UserRepository(db)
    items, total = await repo.list_by_tenant(user.tenant_id, page, page_size)
    pages = (total + page_size - 1) // page_size
    return UserPage(
        items=[
            UserRead(
                id=u.id, email=u.email, full_name=u.full_name,
                phone=u.phone, status=u.status.value,
                roles=[r.slug for r in u.roles],
            )
            for u in items
        ],
        total=total, page=page, page_size=page_size, pages=pages,
    )

@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(USER_UPDATE)),
) -> UserRead:
    """Edita usuário do tenant (nome, telefone, status, roles)."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = UserRepository(db)
    target = await repo.get_by_tenant(user_id, user.tenant_id)
    if not target:
        raise NotFoundError("Usuário não encontrado.")

    data = body.model_dump(exclude_unset=True)
    role_slugs = data.pop("role_slugs", None)
    if "status" in data and data["status"]:
        data["status"] = UserStatus(data["status"])

    target = await repo.update(target, data)

    if role_slugs is not None:
        # Remove roles atuais e atribui as novas (restringidas ao tenant)
        await db.execute(
            user_roles.delete().where(user_roles.c.user_id == target.id)
        )
        for slug in role_slugs:
            role = await _resolve_role(db, slug, user.tenant_id)
            if role:
                await db.execute(
                    user_roles.insert().values(user_id=target.id, role_id=role.id)
                )

    await record_audit(
        db, action="update", entity="user",
        entity_id=target.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return UserRead(
        id=target.id, email=target.email, full_name=target.full_name,
        phone=target.phone, status=target.status.value,
        roles=[r.slug for r in target.roles],
    )

@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(USER_DELETE)),
) -> None:
    """Desativa um usuário do tenant."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    if user_id == user.id:
        raise ValidationError("Você não pode desativar a si mesmo.")
    repo = UserRepository(db)
    target = await repo.get_by_tenant(user_id, user.tenant_id)
    if not target:
        raise NotFoundError("Usuário não encontrado.")
    target.status = UserStatus.INACTIVE
    await record_audit(
        db, action="delete", entity="user",
        entity_id=target.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()