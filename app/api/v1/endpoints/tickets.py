"""Endpoints de tickets (Bloco 9 — seção 26 + fluxo 61).

- Cliente: cria ticket, acompanha, interage (mensagens públicas), anexos.
- Empresa/atendente: lista tickets do tenant, muda status (com histórico),
  atribui responsável, responde (pública ou interna).
- Isolamento por tenant + propriedade em todas as rotas.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database.session import get_db
from app.models import User
from app.repositories.ticket import TicketRepository
from app.schemas.ticket import (
    TicketAssignRequest,
    TicketCreate,
    TicketDetailRead,
    TicketMessageCreate,
    TicketMessageRead,
    TicketPage,
    TicketRead,
    TicketStatusHistoryRead,
    TicketStatusUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/tickets", tags=["Tickets"])

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

async def _get_ticket_for_user(db: AsyncSession, user: User, ticket_id: UUID):
    """Valida tenant + propriedade/permissão (seções 14, 25).

    Mensagem genérica: quem não tem acesso recebe 404 (anti-vazamento).
    """
    repo = TicketRepository(db)
    ticket = await repo.get(ticket_id)
    if not ticket:
        raise NotFoundError("Ticket não encontrado.")
    if user.is_super_admin:
        return ticket
    if user.customer_id:
        if ticket.customer_id != user.customer_id:
            raise NotFoundError("Ticket não encontrado.")
    else:
        if ticket.tenant_id != user.tenant_id:
            raise NotFoundError("Ticket não encontrado.")
    return ticket

@router.post("", response_model=TicketRead, status_code=201)
async def create_ticket(
    body: TicketCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TicketRead:
    """Cliente abre um novo ticket (fluxo 61)."""
    if not user.customer_id:
        raise ForbiddenError("Acesso negado.")
    repo = TicketRepository(db)
    ticket = await repo.create(
        user.customer_id, body.title, body.description, body.category, body.priority
    )
    await record_audit(
        db, action="create", entity="ticket",
        entity_id=ticket.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return ticket

@router.get("", response_model=TicketPage)
async def list_tickets(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TicketPage:
    """Cliente: seus tickets. Atendente: tickets do tenant."""
    repo = TicketRepository(db)
    if user.customer_id:
        items, total = await repo.list_by_customer(user.customer_id, page, page_size)
    else:
        items, total = await repo.list_by_tenant(page, page_size)
    return TicketPage(items=items, total=total, page=page, page_size=page_size)

@router.get("/{ticket_id}", response_model=TicketDetailRead)
async def get_ticket(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TicketDetailRead:
    ticket = await _get_ticket_for_user(db, user, ticket_id)
    repo = TicketRepository(db)
    include_internal = _is_agent(user)
    messages = await repo.list_messages(ticket.id, include_internal)
    history = await repo.list_history(ticket.id)
    data = TicketRead.model_validate(ticket).model_dump()
    return TicketDetailRead(
        **data,
        messages=[TicketMessageRead.model_validate(m) for m in messages],
        history=[TicketStatusHistoryRead.model_validate(h) for h in history],
    )

@router.get("/{ticket_id}/messages", response_model=list[TicketMessageRead])
async def list_messages(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TicketMessageRead]:
    ticket = await _get_ticket_for_user(db, user, ticket_id)
    repo = TicketRepository(db)
    return await repo.list_messages(ticket.id, include_internal=_is_agent(user))

@router.post("/{ticket_id}/messages", response_model=TicketMessageRead, status_code=201)
async def add_message(
    ticket_id: UUID,
    body: TicketMessageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TicketMessageRead:
    ticket = await _get_ticket_for_user(db, user, ticket_id)
    if body.is_internal and not _is_agent(user):
        raise ForbiddenError("Acesso negado.")  # nota interna só da empresa
    repo = TicketRepository(db)
    msg = await repo.add_message(
        ticket.id,
        author_user_id=None if user.customer_id else user.id,
        author_customer_id=user.customer_id,
        content=body.content,
        is_internal=body.is_internal,
    )
    await db.commit()
    return msg

@router.patch("/{ticket_id}/status", response_model=TicketRead)
async def update_status(
    ticket_id: UUID,
    body: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TicketRead:
    """Mudança de status — apenas a empresa (registra histórico, seção 26)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    ticket = await _get_ticket_for_user(db, user, ticket_id)
    repo = TicketRepository(db)
    await repo.update_status(ticket, body.status, body.note)
    await record_audit(
        db, action="update", entity="ticket",
        entity_id=ticket.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    # Re-busca após commit (evita MissingGreenlet na serialização)
    return await repo.get(ticket.id)

@router.post("/{ticket_id}/assign", response_model=TicketRead)
async def assign_ticket(
    ticket_id: UUID,
    body: TicketAssignRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TicketRead:
    """Atribuição de responsável — apenas a empresa (seção 26)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    ticket = await _get_ticket_for_user(db, user, ticket_id)
    repo = TicketRepository(db)
    await repo.assign(ticket, body.assignee_id)
    await db.commit()
    # Re-busca após commit (evita MissingGreenlet na serialização)
    return await repo.get(ticket.id)

@router.post("/{ticket_id}/attachments", response_model=TicketMessageRead, status_code=201)
async def upload_attachment(
    ticket_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TicketMessageRead:
    """Anexo do ticket (fluxo 61): validação (Bloco 6) -> R2 -> metadados -> mensagem."""
    ticket = await _get_ticket_for_user(db, user, ticket_id)

    # Import lazy: assinaturas exatas do Bloco 6 (ver nota de verificação abaixo)
    from app.repositories.file import FileRepository
    from app.services.file_validation import validate_upload
    from app.services.storage import StorageService

    validated = await validate_upload(file)  # (nome_sanitizado, mime, size_bytes)
    storage = StorageService()
    object_key = await storage.upload_public(  # AJUSTAR conforme assinatura real do Bloco 6
        file=file,
        owner_type="ticket",
        owner_id=ticket.id,
        tenant_id=user.tenant_id,
    )
    file_repo = FileRepository(db)
    file_row = await file_repo.create(
        tenant_id=user.tenant_id,
        owner_type="ticket",
        owner_id=ticket.id,
        name=validated[0],
        mime_type=validated[1],
        size_bytes=validated[2],
        storage_key=object_key,
    )
    repo = TicketRepository(db)
    msg = await repo.add_message(
        ticket.id,
        author_user_id=None if user.customer_id else user.id,
        author_customer_id=user.customer_id,
        content="📎 Anexo",
        is_internal=False,
        attachment_file_id=file_row.id,
    )
    await db.commit()
    return msg