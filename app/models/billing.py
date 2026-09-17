"""Modelo de cobrança de assinatura/implantação (integração Asaas).

Representa uma cobrança gerada para a EMPRESA (tenant) pagar a NYD.
- tenant_id: a empresa dona da cobrança (isolamento multi-tenant).
- asaas_payment_id / asaas_subscription_id: IDs no Asaas.
- checkout_url: página de checkout HOSPEDADA do Asaas (prioridade).
- status: espelho do status financeiro da cobrança no Asaas.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import BillingStatus, BillingType, pg_enum
from app.models.mixins import TenantMixin

class BillingCharge(Base, TenantMixin, TimestampMixin):
    """Cobrança da empresa (mensalidade, implantação ou customizada)."""

    __tablename__ = "billing_charges"
    __table_args__ = (
        Index("ix_billing_charges_tenant_status", "tenant_id", "status"),
        Index("ix_billing_charges_tenant_type", "tenant_id", "type"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Tipo da cobrança: mensalidade | implantacao | custom
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Valor em reais (até 14 dígitos, 2 casas decimais)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Data de vencimento (Asaas trabalha com data, não datetime)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Forma de pagamento escolhida: pix | boleto | credit_card
    billing_type: Mapped[BillingType] = mapped_column(
        pg_enum(BillingType, "billing_type"), nullable=False
    )
    # Status financeiro (espelho do Asaas)
    status: Mapped[BillingStatus] = mapped_column(
        pg_enum(BillingStatus, "billing_status"),
        nullable=False,
        default=BillingStatus.PENDING,
        server_default=BillingStatus.PENDING.value,
    )
    # IDs no Asaas
    asaas_payment_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    asaas_subscription_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    # URL do checkout HOSPEDADO do Asaas (prioridade)
    checkout_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Referência externa (rastreio) — ex.: "billing:{company_id}:{type}"
    external_reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    # Timestamp de pagamento (preenchido via webhook)
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )