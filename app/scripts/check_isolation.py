"""Teste manual do isolamento multi-tenant (seções 5, 14 e 52 do doc).

Cenário crítico obrigatório: usuário do Tenant A tentando acessar
recurso do Tenant B → acesso negado.
"""

import asyncio

from app.core.context import TenantContext
from app.core.exceptions import NotFoundError
from app.database.session import async_session_factory
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository

async def main() -> None:
    async with async_session_factory() as session:
        companies = CompanyRepository(session)
        customers = CustomerRepository(session)

        # 1) Cria duas empresas (Tenant A e Tenant B)
        TenantContext.set(tenant_id=None, is_super_admin=True)
        company_a = await companies.create(
            name="Empresa A", cnpj="11111111000111", slug="empresa-a"
        )
        company_b = await companies.create(
            name="Empresa B", cnpj="22222222000122", slug="empresa-b"
        )
        await session.commit()
        print(f"Tenant A: {company_a.id}")
        print(f"Tenant B: {company_b.id}")

        # 2) Cria um cliente no Tenant A
        TenantContext.set(tenant_id=company_a.id, is_super_admin=False)
        customer_a = await customers.create(name="Cliente da Empresa A")
        await session.commit()
        print(f"Cliente do Tenant A: {customer_a.id}")

        # 3) Tenant A lê o próprio cliente -> deve encontrar
        found = await customers.get(customer_a.id)
        print(f"[OK] Tenant A acessou o próprio cliente: {found.name}")

        # 4) CENÁRIO CRÍTICO: Tenant B tenta ler cliente do Tenant A -> NEGADO
        TenantContext.set(tenant_id=company_b.id, is_super_admin=False)
        try:
            await customers.get(customer_a.id)
            print("[FALHOU] Tenant B conseguiu acessar dado do Tenant A!")
            raise SystemExit(1)
        except NotFoundError:
            print("[OK] Tenant B NÃO conseguiu acessar dado do Tenant A (NotFound).")

        # 5) Super admin enxerga tudo
        TenantContext.set(tenant_id=None, is_super_admin=True)
        all_customers = await customers.list()
        print(f"[OK] Super admin vê {len(all_customers)} cliente(s) entre os tenants.")

    TenantContext.reset()

if __name__ == "__main__":
    asyncio.run(main())