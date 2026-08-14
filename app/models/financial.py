from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import FinancialAccountStatus, pg_enum
from app.models.mixins import TenantMixin

class FinancialAccount(Base, TenantMixin, TimestampMixin):
    """Conta financeira sincronizada do ERP (somente leitura no portal, seção 27)."""

    __tablename__ = "financial_accounts"
    __table_args__ = (
        Index("ix_financial_accounts_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_financial_accounts_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[FinancialAccountStatus] = mapped_column(
        pg_enum(FinancialAccountStatus, "financial_account_status"),
        nullable=False,
        default=FinancialAccountStatus.OPEN,
        server_default=FinancialAccountStatus.OPEN.value,
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    order_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Identificador externo do ERP — base da idempotência (seção 30)
    external_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )

class FinancialPayment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "financial_payments"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("financial_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )