"""Modelo de convite para criação de usuários/empresas.

Fluxo:
- Super Admin cria a empresa + admin da empresa (convite por e-mail).
- Admin da empresa convida usuários da própria empresa.
- O convidado clica no link, define a própria senha e o usuário é criado.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import pg_enum

class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class Invitation(Base, TimestampMixin):
    """Convite pendente para criação de usuário."""
    __tablename__ = "invitations"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    # Apenas o hash do token é armazenado (segurança)
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    status: Mapped[InvitationStatus] = mapped_column(
        pg_enum(InvitationStatus, "invitation_status"),
        nullable=False,
        default=InvitationStatus.PENDING,
        server_default=InvitationStatus.PENDING.value,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    invited_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )