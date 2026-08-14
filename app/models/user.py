from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import UserStatus, pg_enum
from app.models.rbac import Role, user_roles

class User(Base, TimestampMixin):
    """Usuário da plataforma.

    Pode ser: funcionário da empresa (tem tenant_id) ou
    Super Admin global (is_super_admin=True).
    """

    __tablename__ = "users"

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
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )
    is_super_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )  # preenchido no Bloco 3 (MFA/TOTP)

    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles,
        lazy="selectin",
    )