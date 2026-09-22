from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import SyncStatus, WebhookStatus, pg_enum
from app.models.mixins import TenantMixin


class ERPIntegration(Base, TenantMixin, TimestampMixin):
    __tablename__ = "erp_integrations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_erp_integrations_id_tenant"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # erp
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class IntegrationConfiguration(Base, TenantMixin, TimestampMixin):
    """Configuração não sensível de um connector."""

    __tablename__ = "integration_configurations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_integration_configurations_integration_tenant",
        ),
        UniqueConstraint(
            "integration_id", name="uq_integration_configurations_integration"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    connector: Mapped[str] = mapped_column(String(80), nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class IntegrationCredential(Base, TenantMixin, TimestampMixin):
    """Credenciais cifradas, separadas da configuração operacional."""

    __tablename__ = "integration_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_integration_credentials_integration_tenant",
        ),
        UniqueConstraint(
            "integration_id", "kind", name="uq_integration_credentials_kind"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    previous_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IntegrationApiKey(Base, TenantMixin, TimestampMixin):
    """Chave de máquina; somente prefixo e hash são persistidos."""

    __tablename__ = "integration_api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_integration_api_keys_integration_tenant",
        ),
        UniqueConstraint("integration_id", name="uq_integration_api_keys_integration"),
        UniqueConstraint("key_hash", name="uq_integration_api_keys_hash"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ExternalEntityMapping(Base, TenantMixin, TimestampMixin):
    """Correlação idempotente entre uma entidade do ERP e uma entidade local."""

    __tablename__ = "external_entity_mappings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_external_entity_mappings_integration_tenant",
        ),
        UniqueConstraint(
            "integration_id",
            "entity_type",
            "external_id",
            name="uq_external_entity_mappings_external",
        ),
        Index(
            "ix_external_entity_mappings_internal",
            "tenant_id",
            "entity_type",
            "internal_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    internal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

class SyncExecution(Base, TenantMixin, TimestampMixin):
    """Registro de cada execução de sincronização (seções 32/33)."""

    __tablename__ = "sync_executions"
    __table_args__ = (
        Index("ix_sync_executions_integration", "integration_id"),
        Index("ix_sync_executions_tenant_created", "tenant_id", "created_at"),
        Index("ix_sync_executions_status_terminal", "status", "terminal_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    integration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("erp_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity: Mapped[str] = mapped_column(String(50), nullable=False)  # products, orders...
    status: Mapped[SyncStatus] = mapped_column(
        pg_enum(SyncStatus, "sync_status"),
        nullable=False,
        default=SyncStatus.PENDING,
        server_default=SyncStatus.PENDING.value,
    )
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replay_of_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sync_executions.id", ondelete="SET NULL"),
        nullable=True,
    )
    cursor: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trigger: Mapped[str] = mapped_column(
        String(30), nullable=False, default="legacy", server_default="legacy"
    )
    correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    items_received: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    unchanged_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    stale_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    skipped_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    item_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    item_errors_truncated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    request_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    run_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class WebhookEvent(Base, TenantMixin, TimestampMixin):
    """Evento de webhook recebido (seção 31)."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_events_integration", "integration_id"),
        UniqueConstraint(
            "integration_id",
            "idempotency_key",
            name="uq_webhook_events_integration_idempotency_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    integration_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("erp_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[WebhookStatus] = mapped_column(
        pg_enum(WebhookStatus, "webhook_status"),
        nullable=False,
        default=WebhookStatus.RECEIVED,
        server_default=WebhookStatus.RECEIVED.value,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    inbox_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("integration_inbox.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    payload_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IntegrationInbox(Base, TenantMixin, TimestampMixin):
    """Entrada durável e idempotente antes de qualquer processamento externo."""

    __tablename__ = "integration_inbox"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_integration_inbox_integration_tenant",
        ),
        UniqueConstraint(
            "integration_id",
            "channel",
            "idempotency_key",
            name="uq_integration_inbox_idempotency",
        ),
        Index("ix_integration_inbox_due", "status", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    capability: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sync_executions.id", ondelete="SET NULL")
    )
    replay_of_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("integration_inbox.id", ondelete="SET NULL")
    )
    payload_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IntegrationSchedule(Base, TenantMixin, TimestampMixin):
    """Agenda persistida por integração/capability, com cursor e jitter."""

    __tablename__ = "integration_schedules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_integration_schedules_integration_tenant",
        ),
        UniqueConstraint(
            "integration_id", "capability", name="uq_integration_schedules_capability"
        ),
        Index("ix_integration_schedules_due", "is_active", "next_run_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    capability: Mapped[str] = mapped_column(String(50), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[dict | None] = mapped_column(JSON)
    jitter_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default=text("30")
    )
    max_batch_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2000, server_default=text("2000")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class IntegrationImportFile(Base, TenantMixin, TimestampMixin):
    """Arquivo aceito e persistido antes do enqueue; processado pelo worker."""

    __tablename__ = "integration_import_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_integration_import_files_integration_tenant",
        ),
        Index("ix_integration_import_files_status", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sync_executions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120))
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    payload_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IntegrationAlert(Base, TenantMixin, TimestampMixin):
    """Alerta operacional persistido e resolvível por integração."""

    __tablename__ = "integration_alerts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "tenant_id"],
            ["erp_integrations.id", "erp_integrations.tenant_id"],
            ondelete="CASCADE",
            name="fk_integration_alerts_integration_tenant",
        ),
        UniqueConstraint(
            "integration_id", "kind", name="uq_integration_alerts_kind"
        ),
        Index("ix_integration_alerts_open", "tenant_id", "status", "severity"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    integration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="warning", server_default="warning"
    )
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sync_executions.id", ondelete="SET NULL")
    )
