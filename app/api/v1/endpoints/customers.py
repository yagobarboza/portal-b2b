"""Endpoints de Clientes (CRUD manual + importação em massa).
- GET/POST /customers          -> listar/criar clientes do tenant
- GET/PATCH/DELETE /customers/{id} -> detalhe/editar/desativar
- POST /customers/import       -> importação em massa (CSV/Excel)
- Isolamento por tenant + RBAC (customers:read/create/update)
"""
import csv
import io
from uuid import UUID
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import CUSTOMER_CREATE, CUSTOMER_READ, CUSTOMER_UPDATE
from app.database.session import get_db
from app.models import User
from app.repositories.customer import CustomerRepository
from app.schemas.customer import (
    CustomerCreate,
    CustomerImportResult,
    CustomerPage,
    CustomerRead,
    CustomerUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/customers", tags=["Clientes"])

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

@router.get("", response_model=CustomerPage)
async def list_customers(
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_READ)),
) -> CustomerPage:
    """Lista clientes do tenant (apenas empresa)."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = CustomerRepository(db)
    items, total = await repo.list(search, page, page_size)
    pages = (total + page_size - 1) // page_size
    return CustomerPage(
        items=items, total=total, page=page, page_size=page_size, pages=pages
    )

@router.post("", response_model=CustomerRead, status_code=201)
async def create_customer(
    body: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_CREATE)),
) -> CustomerRead:
    """Cria um cliente do tenant (cadastro manual)."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = CustomerRepository(db)
    if body.email:
        existing = await repo.get_by_email(body.email)
        if existing:
            raise ValidationError("Já existe um cliente com este e-mail.")
    customer = await repo.create(body.model_dump())
    await record_audit(
        db, action="create", entity="customer",
        entity_id=customer.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return customer

@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_READ)),
) -> CustomerRead:
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = CustomerRepository(db)
    customer = await repo.get(customer_id)
    if not customer:
        raise NotFoundError("Cliente não encontrado.")
    return customer

@router.patch("/{customer_id}", response_model=CustomerRead)
async def update_customer(
    customer_id: UUID,
    body: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_UPDATE)),
) -> CustomerRead:
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = CustomerRepository(db)
    customer = await repo.get(customer_id)
    if not customer:
        raise NotFoundError("Cliente não encontrado.")
    customer = await repo.update(customer, body.model_dump())
    await record_audit(
        db, action="update", entity="customer",
        entity_id=customer.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return customer

@router.delete("/{customer_id}", status_code=204)
async def delete_customer(
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_UPDATE)),
) -> None:
    """Desativa (soft delete) um cliente."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = CustomerRepository(db)
    customer = await repo.get(customer_id)
    if not customer:
        raise NotFoundError("Cliente não encontrado.")
    await repo.delete(customer)
    await record_audit(
        db, action="delete", entity="customer",
        entity_id=customer.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()

@router.post("/import", response_model=CustomerImportResult)
async def import_customers(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_CREATE)),
) -> CustomerImportResult:
    """Importa clientes em massa a partir de CSV (upload)."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    content = await file.read()
    # Validação básica de tamanho (anti-DoS)
    if len(content) > 2 * 1024 * 1024:  # 2 MB
        raise ValidationError("Arquivo excede o limite de 2 MB.")

    repo = CustomerRepository(db)
    created = 0
    skipped = 0
    errors: list[dict] = []

    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            name = (row.get("name") or "").strip()
            email = (row.get("email") or "").strip() or None
            if not name:
                skipped += 1
                errors.append({"row": row, "error": "Nome é obrigatório"})
                continue
            if email:
                existing = await repo.get_by_email(email)
                if existing:
                    skipped += 1
                    continue
            await repo.create(
                {
                    "name": name,
                    "email": email,
                    "phone": (row.get("phone") or "").strip() or None,
                    "document": (row.get("document") or "").strip() or None,
                }
            )
            created += 1
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise ValidationError(f"Falha ao processar o arquivo: {exc}")

    await record_audit(
        db, action="import", entity="customer",
        user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return CustomerImportResult(created=created, skipped=skipped, errors=errors)