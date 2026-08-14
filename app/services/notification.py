"""Serviço de notificações (Bloco 12 — roadmap).

- Cria notificações a partir de eventos (pedidos, tickets, chat, financeiro).
- Usa tenant_id explícito (pode ser chamado fora do contexto de request).
"""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import NotificationType
from app.repositories.notification import NotificationRepository

async def notify_user(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    type_: NotificationType,
    title: str,
    body: str | None = None,
):
    repo = NotificationRepository(db)
    return await repo.create(
        tenant_id, user_id=user_id, customer_id=None,
        type_=type_, title=title, body=body,
    )

async def notify_customer(
    db: AsyncSession,
    tenant_id: UUID,
    customer_id: UUID,
    type_: NotificationType,
    title: str,
    body: str | None = None,
):
    repo = NotificationRepository(db)
    return await repo.create(
        tenant_id, user_id=None, customer_id=customer_id,
        type_=type_, title=title, body=body,
    )