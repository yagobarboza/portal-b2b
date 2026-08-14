"""Endpoints de notificações (Bloco 12 — roadmap).

- Cada usuário/cliente acessa SOMENTE as próprias notificações.
- Sem rota de criação pública: as notificações são geradas pelo backend
  (eventos de pedidos, tickets, chat e financeiro).
- Isolamento por tenant + destinatário em todas as rotas.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db
from app.models import User
from app.repositories.notification import NotificationRepository
from app.schemas.notification import (
    NotificationPage,
    NotificationRead,
    UnreadCountRead,
)

router = APIRouter(prefix="/notifications", tags=["Notificações"])

def _targets(user: User) -> tuple[UUID | None, UUID | None]:
    """Resolve o destinatário: usuário da empresa OU cliente."""
    if user.customer_id:
        return None, user.customer_id
    return user.id, None

@router.get("", response_model=NotificationPage)
async def list_notifications(
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationPage:
    """Lista as notificações do usuário/cliente logado."""
    user_id, customer_id = _targets(user)
    repo = NotificationRepository(db)
    items, total = await repo.list_for_user(
        user_id, customer_id, unread_only, page, page_size
    )
    return NotificationPage(items=items, total=total, page=page, page_size=page_size)

@router.get("/unread-count", response_model=UnreadCountRead)
async def unread_count(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UnreadCountRead:
    user_id, customer_id = _targets(user)
    repo = NotificationRepository(db)
    return UnreadCountRead(unread=await repo.unread_count(user_id, customer_id))

@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationRead:
    """Marca uma notificação como lida (somente a própria)."""
    user_id, customer_id = _targets(user)
    repo = NotificationRepository(db)
    notification = await repo.get_owned(notification_id, user_id, customer_id)
    if not notification:
        raise NotFoundError("Notificação não encontrada.")
    await repo.mark_read(notification)
    await db.commit()
    # Re-busca após commit (evita MissingGreenlet na serialização)
    return await repo.get_owned(notification_id, user_id, customer_id)

@router.post("/read-all", response_model=UnreadCountRead)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UnreadCountRead:
    """Marca todas as notificações do usuário como lidas."""
    user_id, customer_id = _targets(user)
    repo = NotificationRepository(db)
    await repo.mark_all_read(user_id, customer_id)
    await db.commit()
    return UnreadCountRead(unread=0)