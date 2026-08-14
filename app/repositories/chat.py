"""Repositório de chat (Bloco 8 — seções 23, 24, 25).

- Sala automática por cliente.
- Mensagens persistentes no PostgreSQL (fonte da verdade).
- TODAS as queries filtram por tenant_id (isolamento, seção 5).
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import TenantContext
from app.models import ChatMessage, ChatParticipant, ChatRoom, User
from app.models.enums import ChatRoomStatus, ChatSector

class ChatRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    # ---------- Salas ----------
    async def get_or_create_room(
        self, customer_id: UUID, sector: ChatSector = ChatSector.SALES
    ) -> ChatRoom:
        """Sala automática única do cliente (seção 23)."""
        result = await self.db.execute(
            select(ChatRoom)
            .where(
                ChatRoom.tenant_id == self._tenant(),
                ChatRoom.customer_id == customer_id,
            )
            .order_by(ChatRoom.created_at.asc())
            .limit(1)
        )
        room = result.scalars().first()
        if room:
            return room

        room = ChatRoom(
            tenant_id=self._tenant(),
            customer_id=customer_id,
            sector=sector,
            status=ChatRoomStatus.OPEN,
        )
        self.db.add(room)
        await self.db.flush()
        self.db.add(
            ChatParticipant(
                tenant_id=self._tenant(),
                room_id=room.id,
                customer_id=customer_id,
                role="owner",
            )
        )
        await self.db.flush()
        return room

    async def get_room(self, room_id: UUID) -> ChatRoom | None:
        result = await self.db.execute(
            select(ChatRoom).where(
                ChatRoom.id == room_id,
                ChatRoom.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list_rooms_by_customer(self, customer_id: UUID) -> list[ChatRoom]:
        result = await self.db.execute(
            select(ChatRoom)
            .where(
                ChatRoom.tenant_id == self._tenant(),
                ChatRoom.customer_id == customer_id,
            )
            .order_by(ChatRoom.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_rooms_by_tenant(self) -> list[ChatRoom]:
        """Atendentes: todas as salas do tenant."""
        result = await self.db.execute(
            select(ChatRoom)
            .where(ChatRoom.tenant_id == self._tenant())
            .order_by(ChatRoom.created_at.desc())
        )
        return list(result.scalars().all())

    # ---------- Mensagens ----------
    async def create_message(
        self,
        room_id: UUID,
        sender_type: str,
        sender_user_id: UUID | None,
        sender_customer_id: UUID | None,
        content: str,
        attachment_file_id: UUID | None = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            tenant_id=self._tenant(),
            room_id=room_id,
            sender_type=sender_type,
            sender_user_id=sender_user_id,
            sender_customer_id=sender_customer_id,
            content=content,
            attachment_file_id=attachment_file_id,
        )
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def list_messages(
        self, room_id: UUID, page: int = 1, page_size: int = 50
    ) -> tuple[list[ChatMessage], int]:
        base = select(ChatMessage).where(
            ChatMessage.room_id == room_id,
            ChatMessage.tenant_id == self._tenant(),
        )
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(ChatMessage.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def mark_room_read(self, room_id: UUID, reader: User) -> int:
        """Marca como lidas as mensagens do OUTRO lado (seção 23)."""
        if reader.customer_id:
            sender_filter = ChatMessage.sender_type == "user"
        else:
            sender_filter = ChatMessage.sender_type == "customer"
        result = await self.db.execute(
            update(ChatMessage)
            .where(
                ChatMessage.room_id == room_id,
                ChatMessage.tenant_id == self._tenant(),
                ChatMessage.read_at.is_(None),
                sender_filter,
            )
            .values(read_at=datetime.now(timezone.utc))
        )
        return result.rowcount or 0

    async def transfer_room(
        self, room_id: UUID, new_sector: ChatSector, agent_user_id: UUID
    ) -> ChatRoom:
        """Transferência de setor do atendimento (seção 23)."""
        room = await self.get_room(room_id)
        if not room:
            return room
        room.sector = new_sector
        self.db.add(
            ChatParticipant(
                tenant_id=self._tenant(),
                room_id=room.id,
                user_id=agent_user_id,
                role="agent",
            )
        )
        await self.db.flush()
        return room