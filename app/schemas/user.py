"""Schemas de Usuários/Equipe do tenant."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str | None = Field(None, max_length=30)
    role_slug: str = Field(..., min_length=1, max_length=100)

class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=255)
    phone: str | None = Field(None, max_length=30)
    role_slugs: list[str] | None = None
    status: str | None = None

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    phone: str | None = None
    status: str
    roles: list[str] = []

class UserPage(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int
    pages: int