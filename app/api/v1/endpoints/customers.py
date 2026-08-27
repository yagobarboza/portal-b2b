"""Endpoints de Clientes (CRUD manual + importação em massa).
- GET/POST /customers          -> listar/criar clientes do tenant
- GET/PATCH/DELETE /customers/{id} -> detalhe/editar/desativar
- POST /customers/import       -> importação em massa (CSV/Excel)
- Isolamento por tenant + RBAC (customers:read/create/update)
- Ao criar/importar cliente com e-mail: cria convite de acesso (perfil CLIENTE)
  e envia o e-mail de boas-vindas (NYD B2B) em background.
"""
import csv
import io
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_permission
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.invitations import compute_expires_at, generate_invite_token
from app.core.permissions import CUSTOMER_CREATE, CUSTOMER_READ, CUSTOMER_UPDATE
from app.database.session import get_db
from app.models import User
from app.models.company import Company
from app.repositories.customer import CustomerRepository
from app.repositories.invitation import InvitationRepository
from app.schemas.customer import (
    CustomerCreate,
    CustomerImportResult,
    CustomerPage,
    CustomerRead,
    CustomerUpdate,
)
from app.services.audit import record_audit
from app.services.email import send_customer_invite_email
from app.services.rbac import ROLE_CLIENTE

router = APIRouter(prefix="/customers", tags=["Clientes"])

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

def _company_base_url(company: Company | None, settings) -> str:
    """URL base do link de convite (domínio customizado ou FRONTEND_BASE_URL)."""
    base = (
        company.domain
        if company and company.domain
        else (settings.FRONTEND_BASE_URL or "")
    )
    return base.rstrip("/")

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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_CREATE)),
) -> CustomerRead:
    """Cria um cliente do tenant (cadastro manual) + envia convite de acesso."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = CustomerRepository(db)
    if body.email:
        existing = await repo.get_by_email(body.email)
        if existing:
            raise ValidationError("Já existe um cliente com este e-mail.")
    customer = await repo.create(body.model_dump())

    # Convite de acesso ao portal (perfil CLIENTE) — só se tiver e-mail.
    # O aceite cria um User com customer_id vinculado (perfil CLIENTE).
    if body.email:
        settings = get_settings()
        token = generate_invite_token()
        await InvitationRepository(db).create(
            email=body.email,
            full_name=customer.name,
            role_slug=ROLE_CLIENTE,
            token=token,
            expires_at=compute_expires_at(),
            tenant_id=user.tenant_id,
            invited_by=user.id,
            customer_id=customer.id,
        )
        company = await db.get(Company, user.tenant_id) if user.tenant_id else None
        company_name = company.name if company else "Portal B2B"
        base_url = _company_base_url(company, settings)
        invite_url = f"{base_url}/accept-invite?token={token}"
        background_tasks.add_task(
            send_customer_invite_email,
            to_email=body.email,
            invite_url=invite_url,
            company_name=company_name,
            expires_hours=settings.INVITE_TOKEN_EXPIRE_HOURS,
        )

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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CUSTOMER_CREATE)),
) -> CustomerImportResult:
    """Importa clientes em massa a partir de CSV + envia convite a cada um."""
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
    invites: list[dict] = []  # acumula convites para enviar em background

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
            customer = await repo.create(
                {
                    "name": name,
                    "email": email,
                    "phone": (row.get("phone") or "").strip() or None,
                    "document": (row.get("document") or "").strip() or None,
                }
            )
            created += 1
            # Convite de acesso — só para clientes com e-mail
            if email:
                token = generate_invite_token()
                await InvitationRepository(db).create(
                    email=email,
                    full_name=name,
                    role_slug=ROLE_CLIENTE,
                    token=token,
                    expires_at=compute_expires_at(),
                    tenant_id=user.tenant_id,
                    invited_by=user.id,
                    customer_id=customer.id,
                )
                invites.append({"email": email, "token": token})
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise ValidationError(f"Falha ao processar o arquivo: {exc}")

    # Envia convites em background (não bloqueia o request)
    if invites:
        settings = get_settings()
        company = await db.get(Company, user.tenant_id) if user.tenant_id else None
        company_name = company.name if company else "Portal B2B"
        base_url = _company_base_url(company, settings)
        for inv in invites:
            invite_url = f"{base_url}/accept-invite?token={inv['token']}"
            background_tasks.add_task(
                send_customer_invite_email,
                to_email=inv["email"],
                invite_url=invite_url,
                company_name=company_name,
                expires_hours=settings.INVITE_TOKEN_EXPIRE_HOURS,
            )

    await record_audit(
        db, action="import", entity="customer",
        user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return CustomerImportResult(created=created, skipped=skipped, errors=errors)