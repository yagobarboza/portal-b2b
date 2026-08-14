from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import SyncStatus, WebhookStatus, pg_enum
from app.models.mixins import TenantMixin

class ERPIntegration(Base, TenantMixin, TimestampMixin):
    __tablename__ = "erp_integrations"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # erp
    config_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # credenciais cifradas (nunca texto puro)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

class SyncExecution(Base, TenantMixin, TimestampMixin):
    """Registro de cada execução de sincronização (seções 32/33)."""

    __tablename__ = "sync_executions"
    __table_args__ = (
        Index("ix_sync_executions_integration", "integration_id"),
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

class WebhookEvent(Base, TenantMixin, TimestampMixin):
    """Evento de webhook recebido (seção 31)."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_events_integration", "integration_id"),
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
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
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