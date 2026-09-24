"""sync complete RBAC catalog and native tenant roles

Revision ID: a2d9c6e4f817
Revises: f7b1d4e9a536
Create Date: 2026-09-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2d9c6e4f817"
down_revision: Union[str, None] = "f7b1d4e9a536"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Snapshot imutável do catálogo. Migrações não importam o código da aplicação
# para continuarem reproduzíveis mesmo se o catálogo mudar no futuro.
_PERMISSIONS = (
    ("companies:read", "Ver empresas", "companies", "Visualizar dados da empresa"),
    ("companies:manage", "Gerenciar empresas", "companies", "Criar/editar dados da empresa"),
    ("users:read", "Ver usuários", "users", "Listar usuários do tenant"),
    ("users:create", "Criar usuários", "users", "Criar usuários do tenant"),
    ("users:update", "Editar usuários", "users", "Editar usuários do tenant"),
    ("users:delete", "Excluir usuários", "users", "Excluir usuários do tenant"),
    ("customers:read", "Ver clientes", "customers", "Listar clientes do tenant"),
    ("customers:create", "Criar clientes", "customers", "Criar clientes do tenant"),
    ("customers:update", "Editar clientes", "customers", "Editar clientes do tenant"),
    ("products:read", "Ver produtos", "products", "Listar produtos do tenant"),
    ("products:create", "Criar produtos", "products", "Criar produtos do tenant"),
    ("products:update", "Editar produtos", "products", "Editar produtos do tenant"),
    ("products:delete", "Excluir produtos", "products", "Excluir produtos do tenant"),
    ("catalogs:read", "Ver catálogos", "catalogs", "Listar catálogos do tenant"),
    ("catalogs:manage", "Gerenciar catálogos", "catalogs", "Criar/editar catálogos do tenant"),
    ("cart:manage", "Gerenciar carrinho", "cart", "Gerenciar o próprio carrinho"),
    ("orders:read", "Ver pedidos", "orders", "Listar pedidos do tenant"),
    ("orders:create", "Criar pedidos", "orders", "Criar pedidos"),
    ("orders:update", "Editar pedidos", "orders", "Editar pedidos"),
    ("orders:manage", "Gerenciar pedidos", "orders", "Aprovar/processar pedidos"),
    ("tickets:read", "Ver tickets", "tickets", "Listar tickets do tenant"),
    ("tickets:create", "Criar tickets", "tickets", "Abrir tickets"),
    ("tickets:update", "Editar tickets", "tickets", "Atualizar tickets"),
    ("chat:read", "Ver chat", "chat", "Ler mensagens do chat"),
    ("chat:send", "Enviar mensagens", "chat", "Enviar mensagens no chat"),
    ("financial:read", "Ver financeiro", "financial", "Consultar dados financeiros"),
    ("files:upload", "Enviar arquivos", "files", "Fazer upload de arquivos"),
    ("files:read", "Ver arquivos", "files", "Acessar arquivos"),
    ("notifications:read", "Ver notificações", "notifications", "Ler notificações"),
    ("admin:manage", "Administração", "admin", "Funções administrativas do tenant"),
    ("billing:read", "Ver cobranças", "billing", "Ver cobranças/assinaturas da empresa"),
    ("billing:manage", "Gerenciar cobranças", "billing", "Criar/gerenciar cobranças (Super Admin)"),
    ("integrations:read", "Ver integrações", "integrations", "Visualizar integrações e execuções do tenant"),
    ("integrations:manage", "Gerenciar integrações", "integrations", "Criar e configurar integrações do tenant"),
    ("integrations:run", "Executar integrações", "integrations", "Executar importações, pulls e sincronizações"),
    ("integrations:secrets", "Gerenciar segredos de integrações", "integrations", "Emitir, rotacionar e revogar credenciais de integrações"),
    ("super_admin:all", "Super Admin", "admin", "Acesso global à plataforma"),
)


_NATIVE_ROLES = {
    "admin": ("Admin da Empresa", None),
    "vendedor": ("Vendedor", None),
    "financeiro": ("Financeiro", None),
    "suporte": ("Suporte", None),
    "suporte_tecnico": (
        "Suporte Técnico",
        "Gerencia configurações, credenciais e execuções das integrações ERP.",
    ),
    "cliente": ("Cliente", None),
}


_ROLE_GRANTS = {
    "admin": (
        "companies:read", "users:read", "users:create", "users:update", "users:delete",
        "customers:read", "customers:create", "customers:update",
        "products:read", "products:create", "products:update", "products:delete",
        "catalogs:read", "catalogs:manage", "orders:read", "orders:manage",
        "tickets:read", "tickets:update", "chat:read", "chat:send",
        "financial:read", "files:upload", "files:read", "notifications:read",
        "admin:manage", "integrations:read", "integrations:manage",
        "integrations:run", "integrations:secrets", "billing:read", "billing:manage",
    ),
    "vendedor": (
        "customers:read", "customers:create", "customers:update", "products:read",
        "catalogs:read", "orders:read", "orders:create", "orders:update",
        "chat:read", "chat:send", "notifications:read",
    ),
    "financeiro": (
        "financial:read", "orders:read", "customers:read", "notifications:read",
    ),
    "suporte": (
        "tickets:read", "tickets:create", "tickets:update", "chat:read",
        "chat:send", "customers:read", "notifications:read",
    ),
    "suporte_tecnico": (
        "integrations:read", "integrations:manage", "integrations:run",
        "integrations:secrets",
    ),
    "cliente": (
        "products:read", "catalogs:read", "cart:manage", "orders:read",
        "orders:create", "tickets:read", "tickets:create", "chat:read",
        "chat:send", "financial:read", "notifications:read",
    ),
}


def upgrade() -> None:
    for code, name, module, description in _PERMISSIONS:
        op.execute(
            sa.text("""
                INSERT INTO permissions (
                    id, code, name, module, description, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), :code, :name, :module,
                    :description, now(), now()
                )
                ON CONFLICT (code) DO UPDATE
                    SET name = EXCLUDED.name,
                        module = EXCLUDED.module,
                        description = EXCLUDED.description,
                        updated_at = now()
            """).bindparams(
                code=code,
                name=name,
                module=module,
                description=description,
            )
        )

    # Garante as roles nativas em tenants criados antes do fluxo atual.
    for slug, (name, description) in _NATIVE_ROLES.items():
        op.execute(
            sa.text("""
                INSERT INTO roles (
                    id, tenant_id, name, slug, description, is_system,
                    created_at, updated_at
                )
                SELECT gen_random_uuid(), c.id, :name, :slug, :description,
                       true, now(), now()
                  FROM companies c
                ON CONFLICT (tenant_id, slug) DO UPDATE
                    SET name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        is_system = true,
                        updated_at = now()
            """).bindparams(name=name, slug=slug, description=description)
        )

    # Reconciliador aditivo: não remove permissões personalizadas já atribuídas.
    for slug, codes in _ROLE_GRANTS.items():
        quoted_codes = ", ".join(f"'{code}'" for code in codes)
        op.execute(sa.text(f"""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id
              FROM roles r
              CROSS JOIN permissions p
             WHERE r.tenant_id IS NOT NULL
               AND r.slug = :slug
               AND p.code IN ({quoted_codes})
            ON CONFLICT DO NOTHING
        """).bindparams(slug=slug))


def downgrade() -> None:
    # Data migration intentionally non-destructive: revoking permissions on
    # downgrade could lock administrators out of existing production tenants.
    pass
