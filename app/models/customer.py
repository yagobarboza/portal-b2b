from uuid import UUID

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import CustomerStatus, pg_enum
from app.models.mixins import SoftDeleteMixin, TenantMixin

class Customer(Base, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """Cliente de uma empresa (tenant).

    É a entidade que o Cliente do portal representa
    (perfil com acesso ao portal).
    """

    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    document: Mapped[str | None] = mapped_column(
        String(18), nullable=True
    )  # CPF ou CNPJ
    status: Mapped[CustomerStatus] = mapped_column(
        pg_enum(CustomerStatus, "customer_status"),
        nullable=False,
        default=CustomerStatus.ACTIVE,
        server_default=CustomerStatus.ACTIVE.value,
    )