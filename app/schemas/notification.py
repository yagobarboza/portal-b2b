"""Schemas de notificações (Bloco 12 — roadmap)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import NotificationType

class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID | None
    customer_id: UUID | None
    type: NotificationType
    title: str
    body: str | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime

class NotificationPage(BaseModel):
    items: list[NotificationRead]
    total: int
    page: int
    page_size: int

class UnreadCountRead(BaseModel):
    unread: int