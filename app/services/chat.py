"""Serviço de chat (Bloco 8 — seções 23, 24, 25).

- Envio de mensagem: valida acesso, persiste no PostgreSQL e publica no
  Redis Pub/Sub (comunicação entre instâncias, seção 24).
- Redis NÃO é armazenamento permanente — a fonte da verdade é o PostgreSQL.
"""
import json
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models import ChatMessage, ChatRoom, User
from app.repositories.chat import ChatRepository

settings = get_settings()
redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

async def get_chat_room_for_user(
    db: AsyncSession, user: User, room_id: UUID
) -> ChatRoom:
    """Valida acesso do usuário à sala (seção 25).

    Tenant (via repositório) + propriedade/permissão:
    - Cliente: só a própria sala (customer_id).
    - Atendente (usuário do tenant sem customer_id): salas do tenant.
    """
    repo = ChatRepository(db)
    room = await repo.get_room(room_id)
    if not room:
        raise NotFoundError("Sala não encontrada.")
    if user.is_super_admin:
        return room
    if user.customer_id:
        if room.customer_id != user.customer_id:
            raise ForbiddenError("Acesso negado.")
    else:
        if not user.tenant_id or room.tenant_id != user.tenant_id:
            raise ForbiddenError("Acesso negado.")
    return room

def _msg_payload(msg: ChatMessage) -> dict:
    return {
        "id": str(msg.id),
        "room_id": str(msg.room_id),
        "sender_type": msg.sender_type,
        "sender_user_id": str(msg.sender_user_id) if msg.sender_user_id else None,
        "sender_customer_id": str(msg.sender_customer_id) if msg.sender_customer_id else None,
        "content": msg.content,
        "attachment_file_id": str(msg.attachment_file_id) if msg.attachment_file_id else None,
        "read_at": msg.read_at.isoformat() if msg.read_at else None,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }

async def publish_chat_message(room_id: UUID, msg: ChatMessage) -> None:
    """Publica no Redis Pub/Sub (best-effort). A mensagem já está persistida."""
    try:
        await redis_client.publish(f"chat:{room_id}", json.dumps(_msg_payload(msg)))
    except Exception:
        pass  # Pub/Sub é camada de comunicação; PostgreSQL é a fonte da verdade

async def send_chat_message(
    db: AsyncSession,
    room: ChatRoom,
    user: User,
    content: str,
    attachment_file_id: UUID | None = None,
) -> ChatMessage:
    """Persiste a mensagem e publica no Redis (seções 23, 24)."""
    if user.customer_id:
        sender_type, sender_user_id, sender_customer_id = (
            "customer", None, user.customer_id,
        )
    else:
        sender_type, sender_user_id, sender_customer_id = (
            "user", user.id, None,
        )
    repo = ChatRepository(db)
    msg = await repo.create_message(
        room.id, sender_type, sender_user_id, sender_customer_id,
        content, attachment_file_id,
    )
    await db.commit()
    await publish_chat_message(room.id, msg)
    return msg