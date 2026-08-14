from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

# Tabela de associação: Role <-> Permission
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        PGUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        PGUUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Tabela de associação: User <-> Role
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        PGUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

class Permission(Base, TimestampMixin):
    """Catálogo global de permissões do sistema.

    Não pertence a um tenant: é a definição do que EXISTE.
    A atribuição a usuários acontece via Roles do tenant.
    """

    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )  # ex.: orders:read
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    module: Mapped[str] = mapped_column(String(80), nullable=False)  # ex.: orders
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

class Role(Base, TimestampMixin):
    """Papel RBAC.

    tenant_id NULL = papel global do sistema (ex.: super_admin).
    tenant_id preenchido = papel da empresa.
    """

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_roles_tenant_slug"),
        Index(
            "uq_roles_global_slug",
            "slug",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions,
        lazy="selectin",
    )