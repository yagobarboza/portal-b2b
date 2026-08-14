from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import OrderStatus, pg_enum
from app.models.mixins import TenantMixin

class Order(Base, TenantMixin, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_orders_tenant_number"),
        Index("ix_orders_tenant_status", "tenant_id", "status"),
        Index("ix_orders_tenant_customer", "tenant_id", "customer_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus, "order_status"),
        nullable=False,
        default=OrderStatus.DRAFT,
        server_default=OrderStatus.DRAFT.value,
    )
    total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    status_history: Mapped[list[OrderStatusHistory]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

class OrderItem(Base, TenantMixin, TimestampMixin):
    __tablename__ = "order_items"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="items")

class OrderStatusHistory(Base, TenantMixin, TimestampMixin):
    """Histórico de mudanças de status do pedido (seção 22)."""

    __tablename__ = "order_status_history"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[OrderStatus | None] = mapped_column(
        pg_enum(OrderStatus, "order_status"), nullable=True
    )
    to_status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus, "order_status"), nullable=False
    )
    changed_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    order: Mapped[Order] = relationship(back_populates="status_history")