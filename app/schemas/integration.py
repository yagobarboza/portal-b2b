"""Schemas de integrações (Bloco 11 — seções 28-33)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SyncStatus, WebhookStatus

class ERPIntegrationCreate(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    type: str = Field(default="erp", max_length=50)

class ERPIntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    type: str
    is_active: bool
    created_at: datetime

class SyncTriggerRequest(BaseModel):
    entity: str = Field(default="financial", max_length=50)

class SyncExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    integration_id: UUID
    entity: str
    status: SyncStatus
    processed: int
    errors: int
    started_at: datetime | None
    finished_at: datetime | None
    message: str | None
    created_at: datetime

class WebhookEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    integration_id: UUID
    status: WebhookStatus
    received_at: datetime
    processed_at: datetime | None
    error: str | None
    created_at: datetime