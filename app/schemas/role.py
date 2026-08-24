"""Schemas de Roles customizadas do tenant."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class RoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-_]+$")
    description: str | None = None
    permission_codes: list[str] = []

class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    description: str | None = None
    permission_codes: list[str] | None = None

class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: str | None = None
    is_system: bool
    permissions: list[str] = []  # códigos das permissões

class RoleList(BaseModel):
    items: list[RoleRead]