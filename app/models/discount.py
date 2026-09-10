from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import DiscountType, pg_enum
from app.models.mixins import TenantMixin

class QuantityDiscount(Base, TenantMixin, TimestampMixin):
    """Desconto por quantidade (Desconto Progressivo).

    - Regra por PRODUTO: desconto passa a valer a partir de min_quantity unidades.
    - customer_id NULL = vale para TODOS os clientes; preenchido = só aquele cliente.
    - discount_type: "percent" (percentual) | "fixed" (R$ por unidade).
    - Modelo VOLUME (all units): ao atingir a faixa, o desconto vale para TODAS
      as unidades do produto no carrinho.
    """

    __tablename__ = "quantity_discounts"
    __table_args__ = (
        # Regra geral (customer_id NULL) é única por tenant + produto + faixa
        Index(
            "uq_qty_discounts_global",
            "tenant_id", "product_id", "min_quantity",
            unique=True,
            postgresql_where=text("customer_id IS NULL"),
        ),
        # Regra específica de cliente é única por tenant + produto + cliente + faixa
        Index(
            "uq_qty_discounts_customer",
            "tenant_id", "product_id", "customer_id", "min_quantity",
            unique=True,
            postgresql_where=text("customer_id IS NOT NULL"),
        ),
        Index("ix_qty_discounts_product", "tenant_id", "product_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )  # NULL = todos os clientes
    min_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_type: Mapped[DiscountType] = mapped_column(
        pg_enum(DiscountType, "discount_type"),
        nullable=False,
    )
    discount_value: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )