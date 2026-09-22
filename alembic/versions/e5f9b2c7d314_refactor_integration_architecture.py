"""refactor integration architecture

Revision ID: e5f9b2c7d314
Revises: c4e8a1d6f203
Create Date: 2026-09-21
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f9b2c7d314"
down_revision: str | None = "c4e8a1d6f203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _migrate_api_configurations() -> None:
    """Separa somente JSONs válidos; blobs legados inválidos já eram ilegíveis."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("""
            SELECT id, tenant_id, api_config_encrypted
              FROM erp_integrations
             WHERE api_config_encrypted IS NOT NULL
        """)
    ).mappings()
    secret_fields = {"token", "username", "password", "headers"}
    for row in rows:
        try:
            value = json.loads(row["api_config_encrypted"])
        except (TypeError, ValueError):
            value = {
                "legacy_unparsed_config": row["api_config_encrypted"],
                "requires_manual_migration": True,
            }
        if not isinstance(value, dict):
            value = {
                "legacy_unparsed_config": value,
                "requires_manual_migration": True,
            }
        settings = {key: val for key, val in value.items() if key not in secret_fields}
        credentials = {key: value[key] for key in secret_fields if key in value}
        bind.execute(
            sa.text("""
                INSERT INTO integration_configurations (
                    id, tenant_id, integration_id, connector, settings,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), :tenant_id, :integration_id,
                    'rest_json', CAST(:settings AS json), now(), now()
                )
            """),
            {
                "tenant_id": row["tenant_id"],
                "integration_id": row["id"],
                "settings": json.dumps(settings),
            },
        )
        bind.execute(
            sa.text("""
                INSERT INTO integration_credentials (
                    id, tenant_id, integration_id, kind, payload,
                    rotated_at, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), :tenant_id, :integration_id, 'api',
                    CAST(:payload AS json), now(), now(), now()
                )
            """),
            {
                "tenant_id": row["tenant_id"],
                "integration_id": row["id"],
                "payload": json.dumps(credentials),
            },
        )


def upgrade() -> None:
    # Falha de forma explícita antes de criar a constraint; nunca mescla dois
    # produtos existentes silenciosamente.
    op.execute(sa.text(r"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM products
                 GROUP BY tenant_id,
                          upper(regexp_replace(btrim(sku), '\s+', ' ', 'g'))
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Há SKUs duplicados após normalização; resolva-os antes da migração';
            END IF;
        END $$
    """))
    op.add_column(
        "products", sa.Column("normalized_sku", sa.String(length=80), nullable=True)
    )
    op.execute(sa.text(r"""
        UPDATE products
           SET normalized_sku = upper(
                   regexp_replace(btrim(sku), '\s+', ' ', 'g')
               ),
               sku = upper(regexp_replace(btrim(sku), '\s+', ' ', 'g'))
    """))
    op.alter_column("products", "normalized_sku", nullable=False)
    op.drop_constraint("uq_products_tenant_sku", "products", type_="unique")
    op.create_unique_constraint(
        "uq_products_tenant_normalized_sku",
        "products",
        ["tenant_id", "normalized_sku"],
    )

    op.create_unique_constraint(
        "uq_erp_integrations_id_tenant",
        "erp_integrations",
        ["id", "tenant_id"],
    )

    op.create_table(
        "integration_configurations",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("connector", sa.String(length=80), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_integration_configurations_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name=op.f("fk_integration_configurations_tenant_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_configurations")),
        sa.UniqueConstraint(
            "integration_id", name="uq_integration_configurations_integration"
        ),
    )
    op.create_index(
        op.f("ix_integration_configurations_tenant_id"),
        "integration_configurations",
        ["tenant_id"],
    )

    op.create_table(
        "integration_credentials",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("previous_payload", sa.JSON(), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_integration_credentials_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name=op.f("fk_integration_credentials_tenant_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_credentials")),
        sa.UniqueConstraint(
            "integration_id", "kind", name="uq_integration_credentials_kind"
        ),
    )
    op.create_index(
        op.f("ix_integration_credentials_tenant_id"),
        "integration_credentials",
        ["tenant_id"],
    )

    op.create_table(
        "integration_api_keys",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_integration_api_keys_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name=op.f("fk_integration_api_keys_tenant_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_api_keys")),
        sa.UniqueConstraint(
            "integration_id", name="uq_integration_api_keys_integration"
        ),
        sa.UniqueConstraint("key_hash", name="uq_integration_api_keys_hash"),
    )
    op.create_index(
        op.f("ix_integration_api_keys_tenant_id"),
        "integration_api_keys",
        ["tenant_id"],
    )

    op.create_table(
        "external_entity_mappings",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("internal_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_external_entity_mappings_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["companies.id"],
            name=op.f("fk_external_entity_mappings_tenant_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_entity_mappings")),
        sa.UniqueConstraint(
            "integration_id",
            "entity_type",
            "external_id",
            name="uq_external_entity_mappings_external",
        ),
    )
    op.create_index(
        "ix_external_entity_mappings_internal",
        "external_entity_mappings",
        ["tenant_id", "entity_type", "internal_id"],
    )
    op.create_index(
        op.f("ix_external_entity_mappings_tenant_id"),
        "external_entity_mappings",
        ["tenant_id"],
    )

    _migrate_api_configurations()
    op.execute(sa.text("""
        INSERT INTO integration_api_keys (
            id, tenant_id, integration_id, key_hash, key_prefix,
            rotated_at, created_at, updated_at
        )
        SELECT gen_random_uuid(), tenant_id, id, agent_api_key_hash,
               agent_api_key_prefix, now(), now(), now()
          FROM erp_integrations
         WHERE agent_api_key_hash IS NOT NULL
           AND agent_api_key_prefix IS NOT NULL
    """))
    op.execute(sa.text("""
        INSERT INTO integration_credentials (
            id, tenant_id, integration_id, kind, payload,
            previous_payload, rotated_at, created_at, updated_at
        )
        SELECT gen_random_uuid(), tenant_id, id, 'webhook',
               json_build_object('secret', webhook_secret_encrypted),
               CASE WHEN webhook_previous_secret_encrypted IS NOT NULL
                    THEN json_build_object(
                        'secret', webhook_previous_secret_encrypted
                    )
                    ELSE NULL
               END,
               webhook_secret_rotated_at, now(), now()
          FROM erp_integrations
         WHERE webhook_secret_encrypted IS NOT NULL
    """))

    op.drop_constraint(
        "uq_erp_integrations_agent_api_key_hash",
        "erp_integrations",
        type_="unique",
    )
    op.drop_column("erp_integrations", "api_config_encrypted")
    op.drop_column("erp_integrations", "agent_api_key_hash")
    op.drop_column("erp_integrations", "agent_api_key_prefix")
    op.drop_column("erp_integrations", "webhook_secret_encrypted")
    op.drop_column("erp_integrations", "webhook_previous_secret_encrypted")
    op.drop_column("erp_integrations", "webhook_secret_rotated_at")


def downgrade() -> None:
    op.add_column(
        "erp_integrations", sa.Column("api_config_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "erp_integrations", sa.Column("agent_api_key_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "erp_integrations", sa.Column("agent_api_key_prefix", sa.String(16), nullable=True)
    )
    op.add_column(
        "erp_integrations", sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "erp_integrations",
        sa.Column("webhook_previous_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "erp_integrations",
        sa.Column("webhook_secret_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text("""
        UPDATE erp_integrations i
           SET api_config_encrypted = (
               c.settings::jsonb || COALESCE(cr.payload::jsonb, '{}'::jsonb)
           )::text
          FROM integration_configurations c
          LEFT JOIN integration_credentials cr
            ON cr.integration_id = c.integration_id
           AND cr.kind = 'api'
         WHERE i.id = c.integration_id
    """))
    op.execute(sa.text("""
        UPDATE erp_integrations i
           SET agent_api_key_hash = k.key_hash,
               agent_api_key_prefix = k.key_prefix
          FROM integration_api_keys k
         WHERE i.id = k.integration_id
    """))
    op.execute(sa.text("""
        UPDATE erp_integrations i
           SET webhook_secret_encrypted = cr.payload->>'secret',
               webhook_previous_secret_encrypted = cr.previous_payload->>'secret',
               webhook_secret_rotated_at = cr.rotated_at
          FROM integration_credentials cr
         WHERE i.id = cr.integration_id
           AND cr.kind = 'webhook'
    """))
    op.create_unique_constraint(
        "uq_erp_integrations_agent_api_key_hash",
        "erp_integrations",
        ["agent_api_key_hash"],
    )

    op.drop_table("external_entity_mappings")
    op.drop_table("integration_api_keys")
    op.drop_table("integration_credentials")
    op.drop_table("integration_configurations")
    op.drop_constraint(
        "uq_erp_integrations_id_tenant", "erp_integrations", type_="unique"
    )

    op.drop_constraint(
        "uq_products_tenant_normalized_sku", "products", type_="unique"
    )
    op.create_unique_constraint(
        "uq_products_tenant_sku", "products", ["tenant_id", "sku"]
    )
    op.drop_column("products", "normalized_sku")
