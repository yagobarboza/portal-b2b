from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

class TenantMixin:
    """Coluna de isolamento multi-tenant.

    Toda entidade de tenant DEVE herdar este mixin.
    O tenant_id é obrigatório, indexado e com FK para companies.
    """

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

class SoftDeleteMixin:
    """Exclusão lógica: nunca apagar fisicamente dados de negócio."""

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )