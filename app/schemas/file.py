"""Schemas de arquivos (Bloco 6)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    owner_type: str
    owner_id: uuid.UUID
    original_name: str
    mime_type: str
    size_bytes: int
    is_private: bool
    created_at: datetime

class FileUploadResponse(BaseModel):
    file: FileRead
    url: str  # Signed URL temporária

class FileDownloadResponse(BaseModel):
    url: str
    expires_in: int