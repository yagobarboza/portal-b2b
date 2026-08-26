"""Endpoints de arquivos (Bloco 6 — seções 18, 19, 20).

URLs permanentes (não expiram):
- Se R2_PUBLIC_BASE_URL estiver configurado, monta a URL pública do bucket
  (expires_in=0 → sem expiração).
- Fallback: Signed URL (expira em R2_SIGNED_URL_EXPIRY) caso a base pública
  não esteja definida — nada quebra se a config não estiver pronta.
"""
import uuid

from fastapi import APIRouter, Depends, File as FastAPIFile, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_permission
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.permissions import FILE_UPLOAD
from app.database.session import get_db
from app.models import User
from app.models.enums import FileOwnerType
from app.repositories.file import FileRepository
from app.schemas.file import FileDownloadResponse, FileRead, FileUploadResponse
from app.services.audit import record_audit
from app.services.file_validation import validate_upload
from app.services.storage import StorageService

router = APIRouter(prefix="/files", tags=["Arquivos"])

settings = get_settings()

def _file_url(storage_key: str) -> tuple[str, int]:
    """Retorna (url, expires_in).

    - URL PÚBLICA PERMANENTE se R2_PUBLIC_BASE_URL estiver configurado
      (expires_in=0 → não expira, fica acessível para sempre).
    - Signed URL (fallback) caso contrário, com expiração padrão.
    """
    base = (settings.R2_PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        return f"{base}/{storage_key.lstrip('/')}", 0

    storage = StorageService()
    return storage.generate_signed_url(storage_key), settings.R2_SIGNED_URL_EXPIRY

@router.post("/upload/{owner_type}", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    owner_type: FileOwnerType,
    owner_id: uuid.UUID,
    file: UploadFile = FastAPIFile(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(FILE_UPLOAD)),
) -> FileUploadResponse:
    content = await file.read()

    ext, mime_type, size = validate_upload(
        filename=file.filename or "",
        content=content,
        owner_type=owner_type,
    )

    storage = StorageService()
    storage_key = storage.upload_bytes(
        tenant_id=user.tenant_id,
        owner_type=owner_type.value,
        content=content,
        ext=ext,
        content_type=mime_type,
    )

    repo = FileRepository(db)
    file_obj = await repo.create(
        tenant_id=user.tenant_id,
        owner_type=owner_type,
        owner_id=owner_id,
        original_name=file.filename or "",
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=size,
        uploaded_by_user_id=user.id,
        is_private=True,
    )
    await record_audit(
        db, action="upload", entity="file",
        entity_id=file_obj.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()

    url, _ = _file_url(storage_key)
    return FileUploadResponse(file=FileRead.model_validate(file_obj), url=url)

@router.get("/by-owner/{owner_type}/{owner_id}", response_model=list[FileRead])
async def list_files(
    owner_type: FileOwnerType,
    owner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[FileRead]:
    repo = FileRepository(db)
    files = await repo.list_by_owner(owner_type, owner_id)
    return [FileRead.model_validate(f) for f in files]

@router.get("/{file_id}/download", response_model=FileDownloadResponse)
async def download_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileDownloadResponse:
    repo = FileRepository(db)
    file_obj = await repo.get(file_id)
    if not file_obj:
        raise NotFoundError("Arquivo não encontrado.")

    url, expires_in = _file_url(file_obj.storage_key)
    return FileDownloadResponse(url=url, expires_in=expires_in)