"""integration robustness, queues and concurrency

Revision ID: f6a0c3d8e425
Revises: e5f9b2c7d314
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a0c3d8e425"
down_revision: str | None = "e5f9b2c7d314"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.execute("ALTER TYPE sync_status ADD VALUE IF NOT EXISTS 'dead_letter'")
    op.execute("ALTER TYPE webhook_status ADD VALUE IF NOT EXISTS 'processing'")
    op.execute("ALTER TYPE webhook_status ADD VALUE IF NOT EXISTS 'dead_letter'")

    op.add_column(
        "products", sa.Column("stock_updated_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "products", sa.Column("stock_source_version", sa.String(length=120))
    )
    op.execute(
        "UPDATE products SET stock_updated_at = updated_at WHERE stock IS NOT NULL"
    )

    op.add_column(
        "sync_executions",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "sync_executions",
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
    )
    op.add_column(
        "sync_executions", sa.Column("next_retry_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "sync_executions", sa.Column("terminal_at", sa.DateTime(timezone=True))
    )
    op.add_column("sync_executions", sa.Column("replay_of_id", sa.UUID()))
    op.add_column("sync_executions", sa.Column("cursor", sa.JSON()))
    op.create_foreign_key(
        "fk_sync_executions_replay_of_id_sync_executions",
        "sync_executions",
        "sync_executions",
        ["replay_of_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "integration_inbox",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("result", sa.JSON()),
        sa.Column("run_id", sa.UUID()),
        sa.Column("replay_of_id", sa.UUID()),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_integration_inbox_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["sync_executions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["replay_of_id"], ["integration_inbox.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "integration_id",
            "channel",
            "idempotency_key",
            name="uq_integration_inbox_idempotency",
        ),
    )
    op.create_index(
        "ix_integration_inbox_due",
        "integration_inbox",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_integration_inbox_tenant_id", "integration_inbox", ["tenant_id"]
    )

    op.add_column("webhook_events", sa.Column("inbox_id", sa.UUID()))
    op.create_foreign_key(
        "fk_webhook_events_inbox_id_integration_inbox",
        "webhook_events",
        "integration_inbox",
        ["inbox_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_webhook_events_inbox_id", "webhook_events", ["inbox_id"]
    )

    op.create_table(
        "integration_schedules",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("capability", sa.String(length=50), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("cursor", sa.JSON()),
        sa.Column("jitter_seconds", sa.Integer(), server_default="30", nullable=False),
        sa.Column("max_batch_size", sa.Integer(), server_default="2000", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_integration_schedules_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "integration_id",
            "capability",
            name="uq_integration_schedules_capability",
        ),
    )
    op.create_index(
        "ix_integration_schedules_due",
        "integration_schedules",
        ["is_active", "next_run_at"],
    )
    op.create_index(
        "ix_integration_schedules_tenant_id",
        "integration_schedules",
        ["tenant_id"],
    )

    op.create_table(
        "integration_import_files",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120)),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_integration_import_files_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["sync_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_integration_import_files_status",
        "integration_import_files",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_integration_import_files_tenant_id",
        "integration_import_files",
        ["tenant_id"],
    )

    # Converte a configuração existente em agenda durável sem exigir novo save.
    op.execute(sa.text("""
        INSERT INTO integration_schedules (
            id, tenant_id, integration_id, capability, interval_seconds,
            next_run_at, jitter_seconds, max_batch_size, is_active,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), c.tenant_id, c.integration_id, 'stock',
               GREATEST(60, LEAST(86400,
                   CASE
                     WHEN c.settings->>'interval_minutes' ~ '^[0-9]+$'
                     THEN (c.settings->>'interval_minutes')::int * 60
                     ELSE 900
                   END
               )),
               now() + make_interval(
                   secs => abs(hashtext(c.integration_id::text)) % 31
               ),
               30, 2000, i.is_active, now(), now()
          FROM integration_configurations c
          JOIN erp_integrations i ON i.id = c.integration_id
        ON CONFLICT (integration_id, capability) DO NOTHING
    """))
    op.execute(sa.text("""
        INSERT INTO integration_schedules (
            id, tenant_id, integration_id, capability, interval_seconds,
            next_run_at, jitter_seconds, max_batch_size, is_active,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), c.tenant_id, c.integration_id, 'reconciliation',
               86400,
               now() + interval '1 day' + make_interval(
                   secs => abs(hashtext(c.integration_id::text)) % 901
               ),
               900, 20000, i.is_active, now(), now()
          FROM integration_configurations c
          JOIN erp_integrations i ON i.id = c.integration_id
        ON CONFLICT (integration_id, capability) DO NOTHING
    """))


def downgrade() -> None:
    op.drop_table("integration_import_files")
    op.drop_table("integration_schedules")
    op.drop_constraint(
        "uq_webhook_events_inbox_id", "webhook_events", type_="unique"
    )
    op.drop_constraint(
        "fk_webhook_events_inbox_id_integration_inbox",
        "webhook_events",
        type_="foreignkey",
    )
    op.drop_column("webhook_events", "inbox_id")
    op.drop_table("integration_inbox")

    op.drop_constraint(
        "fk_sync_executions_replay_of_id_sync_executions",
        "sync_executions",
        type_="foreignkey",
    )
    op.drop_column("sync_executions", "cursor")
    op.drop_column("sync_executions", "replay_of_id")
    op.drop_column("sync_executions", "terminal_at")
    op.drop_column("sync_executions", "next_retry_at")
    op.drop_column("sync_executions", "max_attempts")
    op.drop_column("sync_executions", "attempt_count")
    op.drop_column("products", "stock_source_version")
    op.drop_column("products", "stock_updated_at")
    # PostgreSQL não remove valores de enum com segurança no downgrade; os
    # valores adicionais permanecem, sem alterar dados/tabelas da versão anterior.
