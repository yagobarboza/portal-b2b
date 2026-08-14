from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import ChatRoomStatus, ChatSector, pg_enum
from app.models.mixins import TenantMixin

class ChatRoom(Base, TenantMixin, TimestampMixin):
    __tablename__ = "chat_rooms"
    __table_args__ = (
        Index("ix_chat_rooms_tenant_customer", "tenant_id", "customer_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    sector: Mapped[ChatSector] = mapped_column(
        pg_enum(ChatSector, "chat_sector"),
        nullable=False,
        default=ChatSector.SALES,
        server_default=ChatSector.SALES.value,
    )
    status: Mapped[ChatRoomStatus] = mapped_column(
        pg_enum(ChatRoomStatus, "chat_room_status"),
        nullable=False,
        default=ChatRoomStatus.OPEN,
        server_default=ChatRoomStatus.OPEN.value,
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="room", cascade="all, delete-orphan", lazy="selectin"
    )

class ChatParticipant(Base, TenantMixin, TimestampMixin):
    __tablename__ = "chat_participants"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    room_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_rooms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(30), nullable=False, default="member"
    )  # member | agent | owner

class ChatMessage(Base, TenantMixin, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    room_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_rooms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # customer | user
    sender_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    sender_customer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attachment_file_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("files.id", ondelete="SET NULL"),
        nullable=True,
    )

    room: Mapped[ChatRoom] = relationship(back_populates="messages")