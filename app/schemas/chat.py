"""Schemas de chat (Bloco 8 — seções 23, 24, 25)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChatSector

class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)

class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    room_id: UUID
    sender_type: str
    sender_user_id: UUID | None
    sender_customer_id: UUID | None
    content: str
    read_at: datetime | None
    attachment_file_id: UUID | None
    created_at: datetime

class ChatMessagePage(BaseModel):
    items: list[ChatMessageRead]
    total: int
    page: int
    page_size: int

class ChatRoomRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    customer_id: UUID
    sector: str
    status: str
    created_at: datetime

class ChatTransferRequest(BaseModel):
    sector: ChatSector