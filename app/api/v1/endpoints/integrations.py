"""Endpoints de integrações ERP (Bloco 11 — seções 28, 32, 33).

- Administração do tenant: criar/listar integrações, disparar syncs,
  consultar execuções (status, quantidades, erros — seção 33).
- Apenas usuários da empresa (sem customer_id) gerenciam integrações.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database.session import get_db
from app.models import User
from app.repositories.integration import IntegrationRepository
from app.schemas.integration import (
    ERPIntegrationCreate,
    ERPIntegrationRead,
    SyncExecutionRead,
    SyncTriggerRequest,
    WebhookEventRead,
)
from app.services.audit import record_audit
from app.services.integration import run_sync

router = APIRouter(prefix="/integrations", tags=["Integrações ERP"])

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

async def _get_integration_for_user(
    db: AsyncSession, user: User, integration_id: UUID
):
    """Valida tenant (mensagem genérica 404 — anti-vazamento)."""
    repo = IntegrationRepository(db)
    integration = await repo.get(integration_id)
    if not integration:
        raise NotFoundError("Integração não encontrada.")
    if not user.is_super_admin and integration.tenant_id != user.tenant_id:
        raise NotFoundError("Integração não encontrada.")
    return integration

@router.post("", response_model=ERPIntegrationRead, status_code=201)
async def create_integration(
    body: ERPIntegrationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ERPIntegrationRead:
    """Cria uma integração no tenant (seção 28)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    repo = IntegrationRepository(db)
    integration = await repo.create(user.tenant_id, body.name, body.type)
    await record_audit(
        db, action="create", entity="integration",
        entity_id=integration.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return await repo.get(integration.id)

@router.get("", response_model=list[ERPIntegrationRead])
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ERPIntegrationRead]:
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    repo = IntegrationRepository(db)
    return await repo.list_for_tenant(user.tenant_id)

@router.post("/{integration_id}/sync", response_model=SyncExecutionRead)
async def trigger_sync(
    integration_id: UUID,
    body: SyncTriggerRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SyncExecutionRead:
    """Dispara uma sincronização manual (seção 32) — idempotente (seção 30)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)
    sync = await run_sync(db, integration, body.entity)
    await record_audit(
        db, action="sync", entity="integration",
        entity_id=integration.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return sync

@router.get("/{integration_id}/syncs", response_model=list[SyncExecutionRead])
async def list_syncs(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SyncExecutionRead]:
    """Histórico de execuções (seção 33: status, processed, errors, mensagem)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    await _get_integration_for_user(db, user, integration_id)
    repo = IntegrationRepository(db)
    return await repo.list_syncs(integration_id)

@router.get("/{integration_id}/webhook-events", response_model=list[WebhookEventRead])
async def list_webhook_events(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[WebhookEventRead]:
    """Eventos de webhook recebidos (seção 31)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    await _get_integration_for_user(db, user, integration_id)
    repo = IntegrationRepository(db)
    return await repo.list_webhook_events(integration_id)