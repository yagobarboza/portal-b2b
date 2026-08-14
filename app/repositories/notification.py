"""Repositório de notificações (Bloco 12 — roadmap).

- Cada usuário/cliente vê SOMENTE as próprias notificações.
- TODAS as queries filtram por tenant_id + destinatário (isolamento, seção 5).
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.models import Notification
from app.models.enums import NotificationType

class NotificationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        customer_id: UUID | None,
        type_: NotificationType,
        title: str,
        body: str | None = None,
    ) -> Notification:
        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            customer_id=customer_id,
            type=type_,
            title=title,
            body=body,
            is_read=False,
        )
        self.db.add(notification)
        await self.db.flush()
        return notification

    async def list_for_user(
        self,
        user_id: UUID | None,
        customer_id: UUID | None,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Notification], int]:
        base = select(Notification).where(Notification.tenant_id == self._tenant())
        if user_id:
            base = base.where(Notification.user_id == user_id)
        elif customer_id:
            base = base.where(Notification.customer_id == customer_id)
        else:
            # Sem destinatário resolvido -> nada (anti-vazamento)
            return [], 0
        if unread_only:
            base = base.where(Notification.is_read.is_(False))
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def unread_count(
        self, user_id: UUID | None, customer_id: UUID | None
    ) -> int:
        base = select(func.count(Notification.id)).where(
            Notification.tenant_id == self._tenant(),
            Notification.is_read.is_(False),
        )
        if user_id:
            base = base.where(Notification.user_id == user_id)
        elif customer_id:
            base = base.where(Notification.customer_id == customer_id)
        else:
            return 0
        return (await self.db.execute(base)).scalar() or 0

    async def get_owned(
        self,
        notification_id: UUID,
        user_id: UUID | None,
        customer_id: UUID | None,
    ) -> Notification | None:
        """Busca garantindo que a notificação pertence ao destinatário."""
        base = select(Notification).where(
            Notification.id == notification_id,
            Notification.tenant_id == self._tenant(),
        )
        if user_id:
            base = base.where(Notification.user_id == user_id)
        elif customer_id:
            base = base.where(Notification.customer_id == customer_id)
        else:
            return None
        result = await self.db.execute(base)
        return result.scalars().first()

    async def mark_read(self, notification: Notification) -> Notification:
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        await self.db.flush()
        return notification

    async def mark_all_read(
        self, user_id: UUID | None, customer_id: UUID | None
    ) -> int:
        stmt = update(Notification).where(
            Notification.tenant_id == self._tenant(),
            Notification.is_read.is_(False),
        )
        if user_id:
            stmt = stmt.where(Notification.user_id == user_id)
        elif customer_id:
            stmt = stmt.where(Notification.customer_id == customer_id)
        else:
            return 0
        stmt = stmt.values(
            is_read=True, read_at=datetime.now(timezone.utc)
        )
        result = await self.db.execute(stmt)
        return result.rowcount or 0