"""Schemas de tickets (Bloco 9 — seção 26)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TicketPriority, TicketStatus

class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    category: str | None = Field(default=None, max_length=100)
    priority: TicketPriority = TicketPriority.MEDIUM

class TicketMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    is_internal: bool = False

class TicketStatusUpdate(BaseModel):
    status: TicketStatus
    note: str | None = Field(default=None, max_length=255)

class TicketAssignRequest(BaseModel):
    assignee_id: UUID

class TicketMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    author_user_id: UUID | None
    author_customer_id: UUID | None
    content: str
    is_internal: bool
    attachment_file_id: UUID | None
    created_at: datetime

class TicketStatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    from_status: TicketStatus | None
    to_status: TicketStatus
    note: str | None
    created_at: datetime

class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: str
    title: str
    description: str | None
    category: str | None
    priority: TicketPriority
    status: TicketStatus
    customer_id: UUID | None
    assignee_id: UUID | None
    created_at: datetime
    updated_at: datetime

class TicketDetailRead(TicketRead):
    messages: list[TicketMessageRead] = []
    history: list[TicketStatusHistoryRead] = []

class TicketPage(BaseModel):
    items: list[TicketRead]
    total: int
    page: int
    page_size: int