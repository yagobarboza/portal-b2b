"""Repositório de tickets (Bloco 9 — seção 26).

- Número sequencial por tenant.
- CRUD de tickets, mensagens, histórico de status e atribuição.
- TODAS as queries filtram por tenant_id (isolamento, seção 5).
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import TenantContext
from app.models import Ticket, TicketMessage, TicketStatusHistory
from app.models.enums import TicketPriority, TicketStatus

class TicketRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def next_number(self) -> str:
        result = await self.db.execute(
            select(func.count(Ticket.id)).where(Ticket.tenant_id == self._tenant())
        )
        count = result.scalar() or 0
        return f"{count + 1:06d}"

    async def create(
        self,
        customer_id: UUID,
        title: str,
        description: str | None,
        category: str | None,
        priority: TicketPriority,
    ) -> Ticket:
        number = await self.next_number()
        ticket = Ticket(
            tenant_id=self._tenant(),
            number=number,
            title=title,
            description=description,
            category=category,
            priority=priority,
            status=TicketStatus.OPEN,
            customer_id=customer_id,
        )
        self.db.add(ticket)
        await self.db.flush()
        # Histórico inicial (seção 26)
        self.db.add(
            TicketStatusHistory(
                tenant_id=self._tenant(),
                ticket_id=ticket.id,
                from_status=None,
                to_status=TicketStatus.OPEN,
                note="Ticket aberto",
            )
        )
        await self.db.flush()
        return ticket

    async def get(self, ticket_id: UUID) -> Ticket | None:
        result = await self.db.execute(
            select(Ticket)
            .options(selectinload(Ticket.messages))
            .where(
                Ticket.id == ticket_id,
                Ticket.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list_by_customer(
        self, customer_id: UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Ticket], int]:
        base = select(Ticket).where(
            Ticket.tenant_id == self._tenant(),
            Ticket.customer_id == customer_id,
        )
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(Ticket.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_by_tenant(
        self, page: int = 1, page_size: int = 20
    ) -> tuple[list[Ticket], int]:
        base = select(Ticket).where(Ticket.tenant_id == self._tenant())
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(Ticket.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update_status(
        self, ticket: Ticket, new_status: TicketStatus, note: str | None = None
    ) -> Ticket:
        old = ticket.status
        ticket.status = new_status
        self.db.add(
            TicketStatusHistory(
                tenant_id=self._tenant(),
                ticket_id=ticket.id,
                from_status=old,
                to_status=new_status,
                note=note,
            )
        )
        await self.db.flush()
        return ticket

    async def assign(self, ticket: Ticket, assignee_id: UUID) -> Ticket:
        ticket.assignee_id = assignee_id
        await self.db.flush()
        return ticket

    async def add_message(
        self,
        ticket_id: UUID,
        author_user_id: UUID | None,
        author_customer_id: UUID | None,
        content: str,
        is_internal: bool = False,
        attachment_file_id: UUID | None = None,
    ) -> TicketMessage:
        msg = TicketMessage(
            tenant_id=self._tenant(),
            ticket_id=ticket_id,
            author_user_id=author_user_id,
            author_customer_id=author_customer_id,
            content=content,
            is_internal=is_internal,
            attachment_file_id=attachment_file_id,
        )
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def list_messages(
        self, ticket_id: UUID, include_internal: bool
    ) -> list[TicketMessage]:
        stmt = select(TicketMessage).where(
            TicketMessage.ticket_id == ticket_id,
            TicketMessage.tenant_id == self._tenant(),
        )
        if not include_internal:
            stmt = stmt.where(TicketMessage.is_internal.is_(False))
        result = await self.db.execute(
            stmt.order_by(TicketMessage.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_history(self, ticket_id: UUID) -> list[TicketStatusHistory]:
        result = await self.db.execute(
            select(TicketStatusHistory)
            .where(
                TicketStatusHistory.ticket_id == ticket_id,
                TicketStatusHistory.tenant_id == self._tenant(),
            )
            .order_by(TicketStatusHistory.created_at.asc())
        )
        return list(result.scalars().all())