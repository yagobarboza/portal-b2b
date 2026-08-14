from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import CartStatus, pg_enum
from app.models.mixins import TenantMixin

class Cart(Base, TenantMixin, TimestampMixin):
    __tablename__ = "carts"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[CartStatus] = mapped_column(
        pg_enum(CartStatus, "cart_status"),
        nullable=False,
        default=CartStatus.OPEN,
        server_default=CartStatus.OPEN.value,
    )

    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart", cascade="all, delete-orphan", lazy="selectin"
    )

class CartItem(Base, TenantMixin, TimestampMixin):
    __tablename__ = "cart_items"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    cart_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("carts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, default=Decimal("1")
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )

    cart: Mapped[Cart] = relationship(back_populates="items")