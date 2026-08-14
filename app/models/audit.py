from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.mixins import TenantMixin

class AuditLog(Base, TenantMixin, TimestampMixin):
    """Trilha de auditoria (seção 44).

    tenant_id e user_id são NULLABLE: eventos de segurança como
    login_failed e logout podem ocorrer sem usuário/tenant
    autenticado (ex.: tentativa de login com e-mail inexistente).
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_entity", "tenant_id", "entity", "entity_id"),
        Index("ix_audit_logs_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Sobrescreve o tenant_id do TenantMixin para permitir NULL
    # (eventos de segurança podem não ter tenant)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # create, update...
    entity: Mapped[str] = mapped_column(String(80), nullable=False)  # order, ticket...
    entity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    old_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)