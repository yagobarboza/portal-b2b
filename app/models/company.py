from uuid import UUID

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import CompanyStatus, pg_enum

class Company(Base, TimestampMixin):
    """Uma empresa cliente da plataforma = um TENANT.

    É a raiz do isolamento: todas as demais entidades
    referenciam companies.id como tenant_id.
    """

    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cnpj: Mapped[str] = mapped_column(String(18), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    status: Mapped[CompanyStatus] = mapped_column(
        pg_enum(CompanyStatus, "company_status"),
        nullable=False,
        default=CompanyStatus.ACTIVE,
        server_default=CompanyStatus.ACTIVE.value,
    )