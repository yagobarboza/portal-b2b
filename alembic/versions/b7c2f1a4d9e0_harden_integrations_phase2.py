"""harden integrations phase 2

Revision ID: b7c2f1a4d9e0
Revises: 27772ef2a0b6
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c2f1a4d9e0"
down_revision: Union[str, None] = "27772ef2a0b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PERMISSIONS = (
    ("integrations:read", "Ver integrações", "Visualizar integrações e execuções do tenant"),
    ("integrations:manage", "Gerenciar integrações", "Criar e configurar integrações do tenant"),
    ("integrations:run", "Executar integrações", "Executar importações, pulls e sincronizações"),
    ("integrations:secrets", "Gerenciar segredos de integrações", "Emitir, rotacionar e revogar credenciais de integrações"),
)


def upgrade() -> None:
    op.execute("ALTER TYPE sync_status ADD VALUE IF NOT EXISTS 'partial'")
    op.add_column("erp_integrations", sa.Column("api_config_encrypted", sa.Text(), nullable=True))
    op.add_column("erp_integrations", sa.Column("agent_api_key_hash", sa.String(length=64), nullable=True))
    op.add_column("erp_integrations", sa.Column("agent_api_key_prefix", sa.String(length=16), nullable=True))
    op.add_column("erp_integrations", sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("erp_integrations", sa.Column("webhook_previous_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("erp_integrations", sa.Column("webhook_secret_rotated_at", sa.DateTime(timezone=True), nullable=True))

    # Migra os dois formatos legados antes de remover a coluna sobrecarregada.
    op.execute(sa.text("""
        UPDATE erp_integrations
           SET agent_api_key_prefix = split_part(config_encrypted, ':', 1),
               agent_api_key_hash = split_part(config_encrypted, ':', 2)
         WHERE config_encrypted ~ '^[^:]{8}:[0-9a-f]{64}$'
    """))
    op.execute(sa.text("""
        UPDATE erp_integrations
           SET api_config_encrypted = config_encrypted
         WHERE config_encrypted IS NOT NULL
           AND config_encrypted !~ '^[^:]{8}:[0-9a-f]{64}$'
    """))
    op.drop_column("erp_integrations", "config_encrypted")
    op.create_unique_constraint(
        "uq_erp_integrations_agent_api_key_hash",
        "erp_integrations",
        ["agent_api_key_hash"],
    )

    op.add_column("webhook_events", sa.Column("idempotency_key", sa.String(length=200), nullable=True))
    op.execute(sa.text("""
        UPDATE webhook_events
           SET idempotency_key = 'legacy-' || id::text
         WHERE idempotency_key IS NULL
    """))
    op.alter_column("webhook_events", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_webhook_events_integration_idempotency_key",
        "webhook_events",
        ["integration_id", "idempotency_key"],
    )

    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM financial_accounts fa
                  JOIN customers c ON c.id = fa.customer_id
                 WHERE c.tenant_id <> fa.tenant_id
            ) THEN
                RAISE EXCEPTION
                    'Migração bloqueada: há contas financeiras ligadas a cliente de outro tenant';
            END IF;
        END $$
    """))
    op.create_unique_constraint("uq_customers_id_tenant", "customers", ["id", "tenant_id"])
    op.drop_constraint(
        "fk_financial_accounts_customer_id_customers",
        "financial_accounts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_financial_accounts_customer_tenant",
        "financial_accounts",
        "customers",
        ["customer_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )

    for code, name, description in _PERMISSIONS:
        op.execute(
            sa.text("""
                INSERT INTO permissions (id, code, name, module, description, created_at, updated_at)
                VALUES (gen_random_uuid(), :code, :name, 'integrations', :description, now(), now())
                ON CONFLICT (code) DO NOTHING
            """).bindparams(code=code, name=name, description=description)
        )

    # Administradores existentes recebem o mesmo conjunto concedido a novos tenants.
    op.execute(sa.text("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
          FROM roles r
          CROSS JOIN permissions p
         WHERE r.slug = 'admin'
           AND r.tenant_id IS NOT NULL
           AND p.code IN (
               'integrations:read', 'integrations:manage',
               'integrations:run', 'integrations:secrets'
           )
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    # PostgreSQL não remove valores de enum com segurança sem recriar o tipo.
    op.add_column("erp_integrations", sa.Column("config_encrypted", sa.Text(), nullable=True))
    op.execute(sa.text("""
        UPDATE erp_integrations
           SET config_encrypted = CASE
               WHEN agent_api_key_hash IS NOT NULL
                   THEN agent_api_key_prefix || ':' || agent_api_key_hash
               ELSE api_config_encrypted
           END
    """))

    op.drop_constraint("fk_financial_accounts_customer_tenant", "financial_accounts", type_="foreignkey")
    op.create_foreign_key(
        "fk_financial_accounts_customer_id_customers",
        "financial_accounts",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("uq_customers_id_tenant", "customers", type_="unique")

    op.drop_constraint(
        "uq_webhook_events_integration_idempotency_key",
        "webhook_events",
        type_="unique",
    )
    op.drop_column("webhook_events", "idempotency_key")

    op.drop_constraint(
        "uq_erp_integrations_agent_api_key_hash",
        "erp_integrations",
        type_="unique",
    )
    op.drop_column("erp_integrations", "webhook_secret_rotated_at")
    op.drop_column("erp_integrations", "webhook_previous_secret_encrypted")
    op.drop_column("erp_integrations", "webhook_secret_encrypted")
    op.drop_column("erp_integrations", "agent_api_key_prefix")
    op.drop_column("erp_integrations", "agent_api_key_hash")
    op.drop_column("erp_integrations", "api_config_encrypted")

    op.execute(sa.text("""
        DELETE FROM role_permissions
         WHERE permission_id IN (
             SELECT id FROM permissions WHERE code LIKE 'integrations:%'
         )
    """))
    op.execute(sa.text("DELETE FROM permissions WHERE code LIKE 'integrations:%'"))
