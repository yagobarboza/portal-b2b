"""Seed dos usuários de teste (Bloco 18).

Cria 2 empresas (tenants), cliente, roles por tenant e os usuários
usados no conftest.py, com as permissões necessárias para os testes.

- agente@teste.com   / Agente@12345   -> Empresa A, role admin (todas as permissões)
- cliente2@teste.com / Cliente@12345  -> Empresa A, vinculado a cliente, role cliente
- usuarioB@teste.com / UsuarioB@12345 -> Empresa B, role admin (todas as permissões)

Idempotente: se já existir (e-mail/slug), pula — não duplica.
"""
import asyncio

from sqlalchemy import select

from app.core.security import hash_password
from app.database.session import async_session_factory
from app.models import Company, Customer, Permission, Role, User
from app.models.enums import CompanyStatus, CustomerStatus, UserStatus

# Set de permissões do perfil CLIENTE (comprador)
CLIENTE_PERMISSIONS = {
    "products:read", "catalogs:read", "cart:manage",
    "orders:create", "orders:read", "tickets:create", "tickets:read",
    "chat:read", "chat:send", "financial:read",
    "notifications:read", "files:read", "files:upload",
}

async def _get_or_create_company(db, *, name, cnpj, slug, domain, color):
    r = await db.execute(select(Company).where(Company.slug == slug))
    company = r.scalar_one_or_none()
    if company:
        return company
    company = Company(
        name=name, cnpj=cnpj, slug=slug, domain=domain,
        primary_color=color, secondary_color="#1A1A1B",
        status=CompanyStatus.ACTIVE,
    )
    db.add(company)
    await db.flush()
    return company

async def _get_or_create_customer(db, company, *, name, email, document):
    r = await db.execute(select(Customer).where(Customer.email == email))
    customer = r.scalar_one_or_none()
    if customer:
        return customer
    customer = Customer(
        tenant_id=company.id, name=name, email=email,
        document=document, status=CustomerStatus.ACTIVE,
    )
    db.add(customer)
    await db.flush()
    return customer

async def _get_or_create_role(db, company, *, slug, name, permissions):
    r = await db.execute(
        select(Role).where(Role.tenant_id == company.id, Role.slug == slug)
    )
    role = r.scalar_one_or_none()
    if role:
        # 🔒 Re-vincula as permissões mesmo se a role já existir (idempotente).
        # Sem isto, roles criadas antes do seed_rbac ficam SEM permissões
        # e todos os require_permission falham com 403.
        role.permissions = permissions
        return role
    role = Role(
        tenant_id=company.id, name=name, slug=slug,
        description=f"Role {name} do tenant de teste",
        is_system=False, permissions=permissions,
    )
    db.add(role)
    await db.flush()
    return role

async def _get_or_create_user(db, *, email, password, full_name, tenant_id, customer_id, roles):
    r = await db.execute(select(User).where(User.email == email))
    user = r.scalar_one_or_none()
    if user:
        return user
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        tenant_id=tenant_id,
        customer_id=customer_id,
        is_super_admin=False,
        status=UserStatus.ACTIVE,
        roles=roles,
    )
    db.add(user)
    await db.flush()
    return user

async def main() -> None:
    async with async_session_factory() as db:
        # 1) Empresas (tenants)
        company_a = await _get_or_create_company(
            db, name="Empresa A Teste", cnpj="11111111111111",
            slug="empresa-a", domain="empresa-a.test", color="#0F4C81",
        )
        company_b = await _get_or_create_company(
            db, name="Empresa B Teste", cnpj="22222222222222",
            slug="empresa-b", domain="empresa-b.test", color="#E30613",
        )

        # 2) Cliente (vinculado à Empresa A)
        customer_a = await _get_or_create_customer(
            db, company_a, name="Cliente A Teste",
            email="cliente@teste.com", document="33333333000133",
        )

        # 3) Permissões existentes no banco (criadas pelo seed_rbac)
        r = await db.execute(select(Permission))
        all_permissions = list(r.scalars().all())
        cliente_permissions = [
            p for p in all_permissions if p.code in CLIENTE_PERMISSIONS
        ]

        # 4) Roles por tenant (admin = todas as permissões)
        admin_a = await _get_or_create_role(
            db, company_a, slug="admin", name="Admin",
            permissions=all_permissions,
        )
        cliente_role = await _get_or_create_role(
            db, company_a, slug="cliente", name="Cliente",
            permissions=cliente_permissions,
        )
        admin_b = await _get_or_create_role(
            db, company_b, slug="admin", name="Admin",
            permissions=all_permissions,
        )

        # 5) Usuários do conftest
        await _get_or_create_user(
            db, email="agente@teste.com", password="Agente@12345",
            full_name="Agente Teste", tenant_id=company_a.id,
            customer_id=None, roles=[admin_a],
        )
        await _get_or_create_user(
            db, email="cliente2@teste.com", password="Cliente@12345",
            full_name="Cliente Teste", tenant_id=company_a.id,
            customer_id=customer_a.id, roles=[cliente_role],
        )
        await _get_or_create_user(
            db, email="usuarioB@teste.com", password="UsuarioB@12345",
            full_name="Usuario B", tenant_id=company_b.id,
            customer_id=None, roles=[admin_b],
        )

        await db.commit()

    print("Seed de usuários de teste concluído.")
    print("agente@teste.com / Agente@12345  (Empresa A, admin)")
    print("cliente2@teste.com / Cliente@12345 (Empresa A, cliente)")
    print("usuarioB@teste.com / UsuarioB@12345 (Empresa B, admin)")

if __name__ == "__main__":
    asyncio.run(main())