"""Seed dos perfis RBAC e catálogo de permissões (seção 13 do doc).

Cria:
- Todas as permissões do catálogo (app.core.permissions)
- Os 6 perfis do sistema com suas permissões.

Idempotente: pode rodar várias vezes sem duplicar.
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import PERMISSION_CATALOG
from app.database.session import async_session_factory
from app.models import Permission, Role, role_permissions
from app.services.rbac import ROLE_DEFINITIONS

async def seed_rbac(session: AsyncSession) -> None:
    # 1) Permissões (idempotente)
    existing_codes = {
        p.code for p in (await session.execute(select(Permission))).scalars()
    }
    for item in PERMISSION_CATALOG:
        if item["code"] not in existing_codes:
            session.add(Permission(**item))
    await session.flush()

    perms = {
        p.code: p for p in (await session.execute(select(Permission))).scalars()
    }

    # 2) Roles de sistema (tenant_id NULL)
    existing_roles = {
        r.slug for r in (await session.execute(select(Role))).scalars()
    }
    for slug, cfg in ROLE_DEFINITIONS.items():
        if slug in existing_roles:
            continue
        role = Role(
            tenant_id=None,  # papel global do sistema
            name=cfg["name"],
            slug=slug,
            is_system=cfg["is_system"],
        )
        session.add(role)
        await session.flush()
        for code in cfg["permissions"]:
            perm = perms.get(code)
            if perm:
                await session.execute(
                    role_permissions.insert().values(
                        role_id=role.id, permission_id=perm.id
                    )
                )
    await session.commit()

async def main() -> None:
    async with async_session_factory() as session:
        await seed_rbac(session)
        roles = (await session.execute(select(Role))).scalars().all()
        perms = (await session.execute(select(Permission))).scalars().all()
        print(f"[OK] {len(perms)} permissões no catálogo")
        print(f"[OK] {len(roles)} perfis criados:")
        for r in roles:
            print(f"   - {r.slug} ({r.name})")

if __name__ == "__main__":
    asyncio.run(main())