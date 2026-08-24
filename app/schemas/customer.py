"""Schemas de Clientes (CRUD + importação em massa)."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class CustomerBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=30)
    document: str | None = Field(None, max_length=18)  # CPF ou CNPJ

class CustomerCreate(CustomerBase):
    pass

class CustomerUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=30)
    document: str | None = Field(None, max_length=18)

class CustomerRead(CustomerBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    created_at: datetime

class CustomerPage(BaseModel):
    items: list[CustomerRead]
    total: int
    page: int
    page_size: int
    pages: int

class CustomerImportResult(BaseModel):
    created: int
    skipped: int
    errors: list[dict]

class CustomerImportRow(BaseModel):
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    document: str | None = None