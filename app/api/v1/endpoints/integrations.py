"""Endpoints de integrações ERP (Bloco 11 — seções 28, 32, 33).

- Administração do tenant: criar/listar integrações, disparar syncs,
  consultar execuções (status, quantidades, erros — seção 33).
- Apenas usuários da empresa (sem customer_id) gerenciam integrações.
- Sync roda em BACKGROUND (fila ARQ — Bloco 16): o request responde
  na hora com status "pending"; o worker executa e atualiza a execução.

✅ BLOCO I1 — integração NÃO nativa (agente do cliente):
- POST /integrations/agent/stock: o AGENTE empurra o estoque real do ERP
  do cliente. Autentica por chave de API (X-API-Key), não por sessão.
- POST/GET/DELETE /integrations/{id}/agent-key: admin do tenant emite,
  consulta e revoga a chave do agente (a chave em claro aparece UMA vez).

✅ BLOCO B2 — integração por ARQUIVO (tipo `file`):
- POST /integrations/{id}/stock/import: o admin envia um CSV/Excel com o
  estoque (colunas sku/stock). Reusa o MESMO motor do agente.

✅ BLOCO B4 — integração por PULL (tipo `api`):
- PUT/GET /integrations/{id}/api-config: grava/lê a config da API do cliente
  (segredos cifrados; a leitura devolve apenas flags de presença).
- POST /integrations/{id}/api-config/test: testa a conexão sem aplicar nada.
- POST /integrations/{id}/api-config/pull: pull manual (síncrono) agora.
- O pull PERIÓDICO é agendado pelo cron do worker (worker/pull_jobs.py).
"""
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_integration_key
from app.core.api_keys import api_key_prefix, generate_api_key
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.queue import enqueue_job
from app.database.session import get_db
from app.models import User
from app.repositories.integration import IntegrationRepository
from app.schemas.integration import (
    AgentApiKeyCreated,
    AgentApiKeyRead,
    ApiPullConfigIn,
    ApiPullConfigRead,
    ApiPullTestResult,
    ERPIntegrationCreate,
    ERPIntegrationRead,
    StockSyncRequest,
    StockSyncResult,
    SyncExecutionRead,
    SyncTriggerRequest,
    WebhookEventRead,
)
from app.services.api_pull import (
    build_stored_config,
    fetch_and_apply_stock,
    masked_config,
    test_connection,
)
from app.services.audit import record_audit
from app.services.integration import run_sync
from app.services.stock_sync import apply_stock_import, apply_stock_sync

logger = logging.getLogger("integrations")

router = APIRouter(prefix="/integrations", tags=["Integrações ERP"])

# Anti-DoS: teto de tamanho do arquivo de estoque (Bloco B2).
STOCK_IMPORT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

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

# ================= BLOCO I1 — AGENTE (chave de API) =================
# ⚠️ Declarado ANTES das rotas com /{integration_id}/... para não colidir
# com o path param (FastAPI casa na ordem de declaração).
@router.post("/agent/stock", response_model=StockSyncResult)
async def ingest_stock(
    body: StockSyncRequest,
    db: AsyncSession = Depends(get_db),
    integration=Depends(require_integration_key),
) -> StockSyncResult:
    """Recebe o lote de estoque empurrado pelo AGENTE do cliente.

    - Autenticação: chave de API (X-API-Key) — sem sessão de usuário.
    - Tenant: SEMPRE o da chave (nunca do payload) — isolamento garantido.
    - Idempotente por `batch_id`: retry do agente não reaplica o lote.
    - Só atualiza produtos existentes do catálogo (nunca cria).
    """
    try:
        result = await apply_stock_sync(
            db,
            integration=integration,
            items=body.items,
            batch_id=body.batch_id,
        )
    except Exception as exc:  # noqa: BLE001 — nunca vaza stack trace ao agente
        await db.rollback()
        logger.exception("Falha ao ingerir estoque da integração %s", integration.id)
        raise ValidationFailedError(
            "Falha ao processar o lote de estoque."
        ) from exc

    await record_audit(
        db,
        action="ingest_stock",
        entity="integration",
        entity_id=integration.id,
        user_id=None,  # origem é o agente (sem usuário)
        tenant_id=integration.tenant_id,
    )
    await db.commit()
    return StockSyncResult(**result)

# ================= BLOCO B2 — ARQUIVO (CSV/Excel) =================
@router.post("/{integration_id}/stock/import", response_model=StockSyncResult)
async def import_stock(
    integration_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StockSyncResult:
    """Importa estoque em massa de um arquivo CSV/Excel (tipo `file`).

    - Autenticação: sessão do usuário da empresa (RBAC implícito: staff).
    - Tenant: da sessão + validação da integração (nunca do arquivo).
    - Colunas aceitas: sku (obrigatório), stock (obrigatório), external_id.
    - Linha inválida NÃO derruba o arquivo: volta em `details` com o nº da linha.
    - Só atualiza produtos existentes do catálogo (nunca cria).
    """
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)

    content = await file.read()
    if not content:
        raise ValidationFailedError("Arquivo vazio.")
    if len(content) > STOCK_IMPORT_MAX_BYTES:
        limit_mb = STOCK_IMPORT_MAX_BYTES // (1024 * 1024)
        raise ValidationFailedError(f"Arquivo excede o limite de {limit_mb} MB.")

    try:
        result = await apply_stock_import(
            db,
            integration=integration,
            filename=file.filename or "",
            content=content,
        )
    except ValidationFailedError:
        await db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 — nunca vaza stack trace ao usuário
        await db.rollback()
        logger.exception(
            "Falha ao importar estoque da integração %s", integration.id
        )
        raise ValidationFailedError(
            "Falha ao processar o arquivo de estoque."
        ) from exc

    await record_audit(
        db,
        action="import_stock",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )
    await db.commit()
    return StockSyncResult(**result)

# ================= BLOCO B4 — PULL (tipo `api`) =================
@router.put("/{integration_id}/api-config", response_model=ApiPullConfigRead)
async def set_api_config(
    integration_id: UUID,
    body: ApiPullConfigIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiPullConfigRead:
    """Grava a config do pull da API do cliente (tipo `api`).

    Segredos (token/username/password/headers) são CIFRADOS antes de persistir.
    A leitura devolve apenas flags de presença — nunca o valor.
    """
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise ValidationFailedError(
            "Configuração de pull só se aplica a integrações do tipo 'api'."
        )

    repo = IntegrationRepository(db)
    stored = build_stored_config(body)
    await repo.set_api_config(integration, stored)
    await record_audit(
        db,
        action="set_api_config",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )
    await db.commit()
    return ApiPullConfigRead(**masked_config(stored))

@router.get("/{integration_id}/api-config", response_model=ApiPullConfigRead)
async def get_api_config(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiPullConfigRead:
    """Lê a config do pull (segredos mascarados — nunca devolvidos)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)

    repo = IntegrationRepository(db)
    cfg = await repo.get_api_config(integration)
    if not cfg:
        raise NotFoundError("Configuração de pull não definida.")
    return ApiPullConfigRead(**masked_config(cfg))

@router.post("/{integration_id}/api-config/test", response_model=ApiPullTestResult)
async def test_api_config(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiPullTestResult:
    """Testa a conexão com a API do cliente SEM aplicar nada no banco."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)

    repo = IntegrationRepository(db)
    cfg = await repo.get_api_config(integration)
    if not cfg:
        raise NotFoundError("Configuração de pull não definida.")

    result = await test_connection(cfg)
    return ApiPullTestResult(**result)

@router.post("/{integration_id}/api-config/pull", response_model=StockSyncResult)
async def pull_stock_now(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StockSyncResult:
    """Executa um PULL manual (síncrono) da API do cliente agora."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)

    repo = IntegrationRepository(db)
    cfg = await repo.get_api_config(integration)
    if not cfg:
        raise NotFoundError("Configuração de pull não definida.")

    try:
        result = await fetch_and_apply_stock(
            db, integration=integration, config=cfg
        )
    except RuntimeError as exc:
        await db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("Falha no pull manual da integração %s", integration.id)
        raise ValidationFailedError("Falha ao executar o pull de estoque.") from exc

    await record_audit(
        db,
        action="pull_stock",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )
    await db.commit()
    return StockSyncResult(**result)

@router.post(
    "/{integration_id}/agent-key",
    response_model=AgentApiKeyCreated,
    status_code=201,
)
async def issue_agent_api_key(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentApiKeyCreated:
    """Emite (ou ROTACIONA) a chave de API do agente desta integração.

    ⚠️ A chave em claro é exibida UMA única vez — o banco guarda só o hash.
    Rotacionar invalida imediatamente a chave anterior.
    """
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)

    repo = IntegrationRepository(db)
    raw_key = generate_api_key()
    await repo.set_agent_api_key(integration, raw_key)

    # Auditoria SEM a chave (nunca registrar segredos).
    await record_audit(
        db,
        action="issue_agent_api_key",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )
    await db.commit()
    return AgentApiKeyCreated(prefix=api_key_prefix(raw_key), api_key=raw_key)

@router.get("/{integration_id}/agent-key", response_model=AgentApiKeyRead)
async def get_agent_api_key(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AgentApiKeyRead:
    """Status da chave do agente (prefixo + ativa). Nunca devolve a chave."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)

    repo = IntegrationRepository(db)
    prefix = await repo.get_agent_api_key_prefix(integration)
    return AgentApiKeyRead(prefix=prefix, is_active=prefix is not None)

@router.delete("/{integration_id}/agent-key", status_code=204)
async def revoke_agent_api_key(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Revoga a chave do agente (o próximo push do agente recebe 401)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)

    repo = IntegrationRepository(db)
    await repo.clear_agent_api_key(integration)
    await record_audit(
        db,
        action="revoke_agent_api_key",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )
    await db.commit()

# ================= BLOCO 11 — administração do tenant =================
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
    """Dispara uma sincronização manual (seção 32) — idempotente (seção 30).

    Processamento em background (fila ARQ — worker):
    - O request responde na hora com a execução "pending".
    - O worker roda `run_sync` (upsert por external_id) e atualiza a
      execução para running/success/failed (seção 33).
    - Fail-open: se o Redis estiver fora, executa de forma SÍNCRONA
      (fallback) para o sync nunca se perder.
    """
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    integration = await _get_integration_for_user(db, user, integration_id)
    enqueued = await enqueue_job(
        "run_sync_job",
        integration_id=str(integration.id),
        entity=body.entity,
    )
    if not enqueued:
        # Fallback síncrono: Redis indisponível — roda agora (não perde o sync).
        sync = await run_sync(db, integration, body.entity)
        await record_audit(
            db, action="sync", entity="integration",
            entity_id=integration.id, user_id=user.id,
            tenant_id=user.tenant_id,
        )
        await db.commit()
        return sync

    await record_audit(
        db, action="sync", entity="integration",
        entity_id=integration.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()

    # Execução enfileirada: resposta imediata com status "pending".
    # A execução real (running/success/failed) aparece em GET .../syncs.
    return SyncExecutionRead(
        id=uuid4(),
        integration_id=integration.id,
        entity=body.entity,
        status="pending",
        processed=0,
        errors=0,
        started_at=None,
        finished_at=None,
        message=None,
        created_at=datetime.now(timezone.utc),
    )

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