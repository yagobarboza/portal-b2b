from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.enums import FileOwnerType, pg_enum
from app.models.mixins import TenantMixin

class File(Base, TenantMixin, TimestampMixin):
    """Metadados de arquivos. O binário fica no Cloudflare R2 (seção 18/19)."""

    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_tenant_owner", "tenant_id", "owner_type", "owner_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_type: Mapped[FileOwnerType] = mapped_column(
        pg_enum(FileOwnerType, "file_owner_type"), nullable=False
    )
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Chave no R2: UUID gerado pelo sistema — NUNCA o nome do usuário (seção 18)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )