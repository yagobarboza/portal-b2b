from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import ProductStatus, pg_enum
from app.models.mixins import SoftDeleteMixin, TenantMixin

# Associação N-N: Catalog <-> Product
catalog_products = Table(
    "catalog_products",
    Base.metadata,
    Column(
        "catalog_id",
        PGUUID(as_uuid=True),
        ForeignKey("catalogs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "product_id",
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "specific_price",
        Numeric(14, 2),
        nullable=True,
    ),  # preço específico dentro do catálogo
)

class Category(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_categories_tenant_slug"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(150), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

class Product(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        Index("ix_products_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00")
    )
    stock: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 3), nullable=True
    )  # sincronizado via ERP
    status: Mapped[ProductStatus] = mapped_column(
        pg_enum(ProductStatus, "product_status"),
        nullable=False,
        default=ProductStatus.ACTIVE,
        server_default=ProductStatus.ACTIVE.value,
    )

    category: Mapped[Category | None] = relationship(lazy="selectin")

class Catalog(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "catalogs"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    products: Mapped[list[Product]] = relationship(
        secondary=catalog_products,
        lazy="selectin",
    )

class PriceList(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """Tabela de preço (seção 17)."""

    __tablename__ = "price_lists"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

class CustomerPrice(Base, TenantMixin, TimestampMixin):
    """Preço específico por cliente (seção 17)."""

    __tablename__ = "customer_prices"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id", "product_id", name="uq_customer_price"
        ),
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
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)