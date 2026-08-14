from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import TicketPriority, TicketStatus, pg_enum
from app.models.mixins import TenantMixin

class Ticket(Base, TenantMixin, TimestampMixin):
    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    priority: Mapped[TicketPriority] = mapped_column(
        pg_enum(TicketPriority, "ticket_priority"),
        nullable=False,
        default=TicketPriority.MEDIUM,
        server_default=TicketPriority.MEDIUM.value,
    )
    status: Mapped[TicketStatus] = mapped_column(
        pg_enum(TicketStatus, "ticket_status"),
        nullable=False,
        default=TicketStatus.OPEN,
        server_default=TicketStatus.OPEN.value,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    messages: Mapped[list[TicketMessage]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", lazy="selectin"
    )

class TicketMessage(Base, TenantMixin, TimestampMixin):
    __tablename__ = "ticket_messages"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ticket_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_customer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )  # visível só para a empresa

    ticket: Mapped[Ticket] = relationship(back_populates="messages")