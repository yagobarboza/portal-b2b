"""integration observability and retention

Revision ID: f7b1d4e9a536
Revises: f6a0c3d8e425
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7b1d4e9a536"
down_revision: str | None = "f6a0c3d8e425"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = [
        sa.Column("trigger", sa.String(30), server_default="legacy", nullable=False),
        sa.Column("correlation_id", sa.String(120)),
        sa.Column("items_received", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stale_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("item_errors", sa.JSON()),
        sa.Column("item_errors_truncated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_class", sa.String(160)),
        sa.Column("retryable", sa.Boolean()),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("request_size_bytes", sa.BigInteger()),
        sa.Column("run_metadata", sa.JSON()),
    ]
    for column in columns:
        op.add_column("sync_executions", column)
    op.create_index(
        "ix_sync_executions_tenant_created",
        "sync_executions",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_sync_executions_status_terminal",
        "sync_executions",
        ["status", "terminal_at"],
    )
    op.execute(sa.text("""
        UPDATE sync_executions
           SET updated_count = processed,
               items_received = processed + errors,
               duration_ms = CASE
                   WHEN started_at IS NOT NULL AND finished_at IS NOT NULL
                   THEN GREATEST(
                       0,
                       (extract(epoch FROM (finished_at - started_at)) * 1000)::bigint
                   )
                   ELSE NULL
               END,
               last_attempt_at = COALESCE(started_at, created_at)
    """))

    op.alter_column("webhook_events", "payload", nullable=True)
    op.add_column(
        "webhook_events",
        sa.Column("payload_purged_at", sa.DateTime(timezone=True)),
    )
    op.alter_column("integration_inbox", "payload", nullable=True)
    op.add_column(
        "integration_inbox",
        sa.Column("payload_purged_at", sa.DateTime(timezone=True)),
    )
    op.alter_column("integration_import_files", "content", nullable=True)
    op.add_column(
        "integration_import_files",
        sa.Column("payload_purged_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "integration_alerts",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
        sa.Column("severity", sa.String(20), server_default="warning", nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_id", sa.UUID()),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            name="fk_integration_alerts_integration_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_run_id"], ["sync_executions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "integration_id", "kind", name="uq_integration_alerts_kind"
        ),
    )
    op.create_index(
        "ix_integration_alerts_open",
        "integration_alerts",
        ["tenant_id", "status", "severity"],
    )
    op.create_index(
        "ix_integration_alerts_tenant_id", "integration_alerts", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_table("integration_alerts")
    op.drop_column("integration_import_files", "payload_purged_at")
    op.alter_column("integration_import_files", "content", nullable=False)
    op.drop_column("integration_inbox", "payload_purged_at")
    op.alter_column("integration_inbox", "payload", nullable=False)
    op.drop_column("webhook_events", "payload_purged_at")
    op.alter_column("webhook_events", "payload", nullable=False)

    op.drop_index("ix_sync_executions_status_terminal", table_name="sync_executions")
    op.drop_index("ix_sync_executions_tenant_created", table_name="sync_executions")
    for name in (
        "run_metadata",
        "request_size_bytes",
        "last_attempt_at",
        "retryable",
        "error_class",
        "error_code",
        "duration_ms",
        "item_errors_truncated",
        "item_errors",
        "skipped_count",
        "stale_count",
        "unchanged_count",
        "updated_count",
        "created_count",
        "items_received",
        "correlation_id",
        "trigger",
    ):
        op.drop_column("sync_executions", name)
