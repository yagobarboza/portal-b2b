"""add technical support native role

Revision ID: c4e8a1d6f203
Revises: b7c2f1a4d9e0
Create Date: 2026-09-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8a1d6f203"
down_revision: Union[str, None] = "b7c2f1a4d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PERMISSIONS = (
    (
        "integrations:read",
        "Ver integrações",
        "Visualizar integrações e execuções do tenant",
    ),
    (
        "integrations:manage",
        "Gerenciar integrações",
        "Criar e configurar integrações do tenant",
    ),
    (
        "integrations:run",
        "Executar integrações",
        "Executar importações, pulls e sincronizações",
    ),
    (
        "integrations:secrets",
        "Gerenciar segredos de integrações",
        "Emitir, rotacionar e revogar credenciais de integrações",
    ),
)


def upgrade() -> None:
    # Mantém o catálogo consistente mesmo em bancos que tiveram carga parcial.
    for code, name, description in _PERMISSIONS:
        op.execute(
            sa.text("""
                INSERT INTO permissions (
                    id, code, name, module, description, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), :code, :name, 'integrations',
                    :description, now(), now()
                )
                ON CONFLICT (code) DO UPDATE
                    SET name = EXCLUDED.name,
                        module = EXCLUDED.module,
                        description = EXCLUDED.description,
                        updated_at = now()
            """).bindparams(code=code, name=name, description=description)
        )

    # O slug é reservado a este perfil nativo. Em caso de uma carga anterior,
    # normaliza a definição sem remover usuários ou permissões já vinculados.
    op.execute(sa.text("""
        INSERT INTO roles (
            id, tenant_id, name, slug, description, is_system,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), c.id, 'Suporte Técnico', 'suporte_tecnico',
               'Gerencia configurações, credenciais e execuções das integrações ERP.',
               true, now(), now()
          FROM companies c
        ON CONFLICT (tenant_id, slug) DO UPDATE
            SET name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_system = true,
                updated_at = now()
    """))

    # Admin e Suporte Técnico recebem as quatro capacidades de integração.
    op.execute(sa.text("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
          FROM roles r
          CROSS JOIN permissions p
         WHERE r.tenant_id IS NOT NULL
           AND r.slug IN ('admin', 'suporte_tecnico')
           AND p.code IN (
               'integrations:read', 'integrations:manage',
               'integrations:run', 'integrations:secrets'
           )
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    # As permissões e os grants do Admin pertencem à migração anterior.
    # Remover a role apaga seus vínculos por cascata.
    op.execute(sa.text("""
        DELETE FROM roles
         WHERE slug = 'suporte_tecnico'
           AND tenant_id IS NOT NULL
           AND is_system = true
    """))
