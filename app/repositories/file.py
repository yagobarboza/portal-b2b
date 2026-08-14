"""Repositório de metadados de arquivos (Bloco 6).

TODAS as queries filtram por tenant_id (isolamento, seção 5).
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.models import File
from app.models.enums import FileOwnerType

class FileRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> uuid.UUID | None:
        return TenantContext.tenant_id()

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: FileOwnerType,
        owner_id: uuid.UUID,
        original_name: str,
        storage_key: str,
        mime_type: str,
        size_bytes: int,
        uploaded_by_user_id: uuid.UUID,
        is_private: bool = True,
    ) -> File:
        obj = File(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            original_name=original_name,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            uploaded_by_user_id=uploaded_by_user_id,
            is_private=is_private,
        )
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get(self, file_id: uuid.UUID) -> File | None:
        result = await self.db.execute(
            select(File).where(
                File.id == file_id,
                File.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list_by_owner(
        self, owner_type: FileOwnerType, owner_id: uuid.UUID
    ) -> list[File]:
        result = await self.db.execute(
            select(File).where(
                File.tenant_id == self._tenant(),
                File.owner_type == owner_type,
                File.owner_id == owner_id,
            )
        )
        return list(result.scalars().all())