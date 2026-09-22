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
- POST /integrations/{id}/api-config/pull: persiste e enfileira o pull manual.
- O pull PERIÓDICO é agendado pelo cron do worker (worker/pull_jobs.py).
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_integration_key, require_permission
from app.core.api_keys import api_key_prefix, generate_api_key
from app.core.config import get_settings
from app.core.crypto import encrypt_str
from app.core.exceptions import (
    FeatureUnavailableError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.core.permissions import (
    INTEGRATION_MANAGE,
    INTEGRATION_READ,
    INTEGRATION_RUN,
    INTEGRATION_SECRETS,
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
    ERPIntegrationUpdate,
    InboxAccepted,
    IntegrationDashboardRead,
    IntegrationDryRunRequest,
    IntegrationDryRunResult,
    ReplayRequest,
    StockSyncRequest,
    StockSyncResult,
    SyncExecutionRead,
    SyncExecutionPage,
    SyncTriggerRequest,
    WebhookSecretCreated,
    WebhookSecretRead,
    WebhookEventRead,
)
from app.services.api_pull import (
    build_stored_config,
    masked_config,
    test_connection,
)
from app.services.audit import record_audit
from app.services.integration import generate_webhook_secret
from app.services.stock_sync import apply_stock_sync

logger = logging.getLogger("integrations")
settings = get_settings()

router = APIRouter(prefix="/integrations", tags=["Integrações ERP"])

# Anti-DoS: teto de tamanho do arquivo de estoque (Bloco B2).
STOCK_IMPORT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

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

async def _mark_sync_failed(
    db: AsyncSession, sync_id: UUID, message: str
) -> None:
    repo = IntegrationRepository(db)
    failed = await repo.get_sync(sync_id)
    if failed:
        from app.services.integration_observability import set_run_failure

        set_run_failure(
            failed,
            status="failed",
            code="queue_unavailable",
            retryable=True,
            message=message,
        )
        await db.commit()

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
    repo = IntegrationRepository(db)
    payload = body.model_dump(mode="json")
    idempotency_key = body.batch_id or f"payload:{repo.payload_hash(payload)}"
    try:
        inbox, created = await repo.create_or_get_inbox(
            integration=integration,
            channel="agent",
            capability="stock",
            idempotency_key=idempotency_key,
            payload=payload,
        )
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    if not created:
        await db.commit()
        if inbox.result:
            duplicate = dict(inbox.result)
            duplicate["status"] = "duplicate"
            return StockSyncResult(**duplicate)
        if inbox.run_id is None:
            raise ServiceUnavailableError("Lote aceito sem execução associada.")
        if inbox.status == "dead_letter":
            return StockSyncResult(
                sync_id=inbox.run_id,
                status="failed",
                processed=0,
                unchanged=0,
                stale=0,
                errors=1,
                message="Lote em estado terminal; solicite um replay autorizado.",
            )
        return StockSyncResult(
            sync_id=inbox.run_id,
            status="duplicate",
            processed=0,
            unchanged=0,
            stale=0,
            errors=0,
            message="Lote já aceito e em processamento.",
        )

    sync = await repo.create_sync(
        integration.id,
        integration.tenant_id,
        "stock",
        trigger="agent",
        correlation_id=idempotency_key[:120],
        request_size_bytes=len(str(payload).encode("utf-8")),
    )
    inbox.run_id = sync.id
    inbox.status = "processing"
    inbox.attempts = 1
    inbox.locked_at = datetime.now(timezone.utc)
    sync.attempt_count = 1
    sync_id = sync.id
    inbox_id = inbox.id
    await db.commit()

    try:
        result = await apply_stock_sync(
            db,
            integration=integration,
            items=body.items,
            sync_execution=sync,
        )
        inbox = await repo.get_inbox(inbox_id)
        assert inbox is not None
        inbox.status = "succeeded"
        inbox.result = StockSyncResult(**result).model_dump(mode="json")
        inbox.processed_at = datetime.now(timezone.utc)
        inbox.locked_at = None
        await record_audit(
            db,
            action="ingest_stock",
            entity="integration",
            entity_id=integration.id,
            user_id=None,  # origem é o agente (sem usuário)
            tenant_id=integration.tenant_id,
        )
        from app.integrations.metrics import observe_terminal_run
        from app.services.integration_dashboard import evaluate_integration_alerts

        await evaluate_integration_alerts(db, integration)
        await db.commit()
        observe_terminal_run(sync)
    except Exception as exc:  # noqa: BLE001 — nunca vaza stack trace ao agente
        await db.rollback()
        inbox = await repo.get_inbox(inbox_id)
        if inbox:
            inbox.status = "retry"
            inbox.available_at = datetime.now(timezone.utc)
            inbox.locked_at = None
            inbox.last_error = "Falha ao processar o lote de estoque."
        pending = await repo.get_sync(sync_id)
        if pending:
            pending.status = "pending"
            pending.message = "Processamento será retomado pelo worker."
        await db.commit()
        await enqueue_job("process_inbox_job", inbox_id=str(inbox_id))
        logger.exception("Falha ao ingerir estoque da integração %s", integration.id)
        raise ValidationFailedError(
            "Falha ao processar o lote de estoque."
        ) from exc
    return StockSyncResult(**result)

# ================= BLOCO B2 — ARQUIVO (CSV/Excel) =================
@router.post(
    "/{integration_id}/stock/import",
    response_model=SyncExecutionRead,
    status_code=202,
)
async def import_stock(
    integration_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> SyncExecutionRead:
    """Importa estoque em massa de um arquivo CSV/Excel (tipo `file`).

    - Autenticação: sessão do usuário da empresa (RBAC implícito: staff).
    - Tenant: da sessão + validação da integração (nunca do arquivo).
    - Colunas aceitas: sku (obrigatório), stock (obrigatório), external_id.
    - Linha inválida NÃO derruba o arquivo: volta em `details` com o nº da linha.
    - Só atualiza produtos existentes do catálogo (nunca cria).
    """
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "file":
        raise ValidationFailedError(
            "Importação de arquivo só se aplica a integrações do tipo 'file'."
        )

    repo = IntegrationRepository(db)
    content = await file.read(STOCK_IMPORT_MAX_BYTES + 1)
    if not content:
        raise ValidationFailedError("Arquivo vazio.")
    if len(content) > STOCK_IMPORT_MAX_BYTES:
        limit_mb = STOCK_IMPORT_MAX_BYTES // (1024 * 1024)
        raise ValidationFailedError(f"Arquivo excede o limite de {limit_mb} MB.")

    sync = await repo.create_sync(
        integration.id,
        integration.tenant_id,
        "stock_file",
        trigger="file",
        correlation_id=f"upload:{uuid4()}",
        request_size_bytes=len(content),
        run_metadata={
            "filename": (file.filename or "estoque.csv")[:255],
            "content_type": (file.content_type or "application/octet-stream")[:120],
        },
    )
    await repo.create_import_file(
        integration=integration,
        run_id=sync.id,
        filename=file.filename or "estoque.csv",
        content_type=file.content_type,
        content=content,
    )
    await record_audit(
        db,
        action="import_stock_accepted",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )
    await db.commit()
    # Se Redis estiver indisponível, o dispatcher periódico recolhe o arquivo.
    await enqueue_job("process_stock_file_job", run_id=str(sync.id))
    return await repo.get_sync(sync.id)

# ================= BLOCO B4 — PULL (tipo `api`) =================
@router.put("/{integration_id}/api-config", response_model=ApiPullConfigRead)
async def set_api_config(
    integration_id: UUID,
    body: ApiPullConfigIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
) -> ApiPullConfigRead:
    """Grava a config do pull da API do cliente (tipo `api`).

    Segredos (token/username/password/headers) são CIFRADOS antes de persistir.
    A leitura devolve apenas flags de presença — nunca o valor.
    """
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise ValidationFailedError(
            "Configuração de pull só se aplica a integrações do tipo 'api'."
        )

    repo = IntegrationRepository(db)
    existing = await repo.get_api_config(integration)
    try:
        stored = build_stored_config(body, existing)
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await repo.set_api_config(integration, stored)
    await repo.upsert_schedule(
        integration=integration,
        capability="stock",
        interval_seconds=body.interval_minutes * 60,
        jitter_seconds=30,
        max_batch_size=2000,
        next_run_at=datetime.now(timezone.utc) + timedelta(minutes=body.interval_minutes),
    )
    await repo.upsert_schedule(
        integration=integration,
        capability="reconciliation",
        interval_seconds=24 * 3600,
        jitter_seconds=15 * 60,
        max_batch_size=20_000,
        next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
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
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
) -> ApiPullConfigRead:
    """Lê a config do pull (segredos mascarados — nunca devolvidos)."""
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise ValidationFailedError(
            "Configuração de pull só se aplica a integrações do tipo 'api'."
        )

    repo = IntegrationRepository(db)
    cfg = await repo.get_api_config(integration)
    if not cfg:
        raise NotFoundError("Configuração de pull não definida.")
    return ApiPullConfigRead(**masked_config(cfg))

@router.post("/{integration_id}/api-config/test", response_model=ApiPullTestResult)
async def test_api_config(
    integration_id: UUID,
    body: ApiPullConfigIn | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
    _run_user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> ApiPullTestResult:
    """Testa a conexão com a API do cliente SEM aplicar nada no banco."""
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise ValidationFailedError("Teste só se aplica a integrações do tipo 'api'.")

    repo = IntegrationRepository(db)
    existing = await repo.get_api_config(integration)
    if body is not None:
        try:
            cfg = build_stored_config(body, existing)
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc
    else:
        cfg = existing
    if not cfg:
        raise NotFoundError("Configuração de pull não definida.")

    result = await test_connection(cfg)
    return ApiPullTestResult(**result)


@router.post(
    "/{integration_id}/api-config/dry-run",
    response_model=IntegrationDryRunResult,
)
async def dry_run_api_config(
    integration_id: UUID,
    body: IntegrationDryRunRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
    _run_user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> IntegrationDryRunResult:
    """Busca e normaliza uma amostra sem salvar configuração nem aplicar dados."""
    from app.core.redaction import redact_text
    from app.services.integration_preview import preview_connector

    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise ValidationFailedError("Dry-run só se aplica a integrações do tipo 'api'.")
    repo = IntegrationRepository(db)
    existing = await repo.get_api_config(integration)
    if body.config is not None:
        try:
            config = build_stored_config(body.config, existing)
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc
    else:
        config = existing
    if not config:
        raise NotFoundError("Configuração de pull não definida.")
    try:
        result = await preview_connector(
            config, entity=body.entity, sample_size=body.sample_size
        )
    except Exception as exc:  # noqa: BLE001 - retorno controlado e redigido
        result = {
            "ok": False,
            "entity": body.entity,
            "received": 0,
            "valid": 0,
            "errors": 1,
            "message": redact_text(
                f"Dry-run não concluído ({exc.__class__.__name__}).", limit=200
            ),
            "sample": [],
            "details": [{"code": "dry_run_failed", "error": "Não foi possível validar a fonte."}],
        }
    return IntegrationDryRunResult(**result)

@router.post(
    "/{integration_id}/api-config/pull",
    response_model=SyncExecutionRead,
    status_code=202,
)
async def pull_stock_now(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> SyncExecutionRead:
    """Persiste e enfileira um pull manual com a mesma política do scheduler."""
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise ValidationFailedError("Pull só se aplica a integrações do tipo 'api'.")

    repo = IntegrationRepository(db)
    cfg = await repo.get_api_config(integration)
    if not cfg:
        raise NotFoundError("Configuração de pull não definida.")

    sync = await repo.create_sync(
        integration.id, integration.tenant_id, "stock", trigger="manual"
    )
    sync_id = sync.id
    await record_audit(
        db,
        action="pull_stock_accepted",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
    )
    await db.commit()
    enqueued = await enqueue_job(
        "run_sync_job",
        integration_id=str(integration.id),
        entity="stock",
        run_id=str(sync_id),
    )
    if not enqueued:
        await _mark_sync_failed(db, sync_id, "Não foi possível enfileirar o pull.")
        raise ServiceUnavailableError("Não foi possível enfileirar o pull.")
    return await repo.get_sync(sync_id)

@router.post(
    "/{integration_id}/agent-key",
    response_model=AgentApiKeyCreated,
    status_code=201,
)
async def issue_agent_api_key(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
) -> AgentApiKeyCreated:
    """Emite (ou ROTACIONA) a chave de API do agente desta integração.

    ⚠️ A chave em claro é exibida UMA única vez — o banco guarda só o hash.
    Rotacionar invalida imediatamente a chave anterior.
    """
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "agent":
        raise ValidationFailedError("Chave de agente só se aplica ao tipo 'agent'.")

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
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
) -> AgentApiKeyRead:
    """Status da chave do agente (prefixo + ativa). Nunca devolve a chave."""
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "agent":
        raise ValidationFailedError("Chave de agente só se aplica ao tipo 'agent'.")

    repo = IntegrationRepository(db)
    prefix = await repo.get_agent_api_key_prefix(integration)
    return AgentApiKeyRead(prefix=prefix, is_active=prefix is not None)

@router.delete("/{integration_id}/agent-key", status_code=204)
async def revoke_agent_api_key(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
) -> None:
    """Revoga a chave do agente (o próximo push do agente recebe 401)."""
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "agent":
        raise ValidationFailedError("Chave de agente só se aplica ao tipo 'agent'.")

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

# ================= SEGREDO POR INTEGRAÇÃO WEBHOOK =================
@router.post(
    "/{integration_id}/webhook-secret",
    response_model=WebhookSecretCreated,
    status_code=201,
)
async def rotate_webhook_secret(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
) -> WebhookSecretCreated:
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "webhook":
        raise ValidationFailedError("Segredo de webhook só se aplica ao tipo 'webhook'.")

    raw_secret = generate_webhook_secret()
    now = datetime.now(timezone.utc)
    repo = IntegrationRepository(db)
    existing_credential = await repo.get_webhook_credential(integration)
    had_previous = bool(
        existing_credential and (existing_credential.payload or {}).get("secret")
    )
    await repo.rotate_webhook_secret(
        integration,
        encrypted_secret=encrypt_str(raw_secret),
        rotated_at=now,
    )
    await record_audit(
        db,
        action="rotate_webhook_secret",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=integration.tenant_id,
    )
    await db.commit()
    previous_valid_until = (
        now + timedelta(seconds=settings.WEBHOOK_SECRET_ROTATION_GRACE_SECONDS)
        if had_previous
        else None
    )
    return WebhookSecretCreated(
        secret=raw_secret,
        rotated_at=now,
        previous_valid_until=previous_valid_until,
    )

@router.get(
    "/{integration_id}/webhook-secret",
    response_model=WebhookSecretRead,
)
async def get_webhook_secret_status(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_SECRETS)),
) -> WebhookSecretRead:
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "webhook":
        raise ValidationFailedError("Segredo de webhook só se aplica ao tipo 'webhook'.")
    repo = IntegrationRepository(db)
    credential = await repo.get_webhook_credential(integration)
    previous_valid_until = None
    if (
        credential
        and credential.previous_payload
        and credential.rotated_at
    ):
        previous_valid_until = credential.rotated_at + timedelta(
            seconds=settings.WEBHOOK_SECRET_ROTATION_GRACE_SECONDS
        )
    return WebhookSecretRead(
        configured=bool(credential and (credential.payload or {}).get("secret")),
        rotated_at=credential.rotated_at if credential else None,
        previous_valid_until=previous_valid_until,
    )

# ================= BLOCO 11 — administração do tenant =================
@router.post("", response_model=ERPIntegrationRead, status_code=201)
async def create_integration(
    body: ERPIntegrationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_MANAGE)),
) -> ERPIntegrationRead:
    """Cria uma integração no tenant (seção 28)."""
    if user.tenant_id is None:
        raise ForbiddenError("Selecione um tenant para criar a integração.")
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
    user: User = Depends(require_permission(INTEGRATION_READ)),
) -> list[ERPIntegrationRead]:
    if user.tenant_id is None:
        return []
    repo = IntegrationRepository(db)
    return await repo.list_for_tenant(user.tenant_id)


@router.get("/dashboard", response_model=IntegrationDashboardRead)
async def integration_dashboard(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_READ)),
) -> IntegrationDashboardRead:
    """Resumo operacional tenant-scoped, sem payloads ou credenciais."""
    from app.services.integration_dashboard import build_integration_dashboard

    if user.tenant_id is None:
        return IntegrationDashboardRead(
            generated_at=datetime.now(timezone.utc), integrations=[], alerts=[]
        )
    return IntegrationDashboardRead(
        **(await build_integration_dashboard(db, user.tenant_id))
    )


@router.get("/{integration_id}", response_model=ERPIntegrationRead)
async def get_integration(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_READ)),
) -> ERPIntegrationRead:
    return await _get_integration_for_user(db, user, integration_id)


@router.patch("/{integration_id}", response_model=ERPIntegrationRead)
async def update_integration(
    integration_id: UUID,
    body: ERPIntegrationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_MANAGE)),
) -> ERPIntegrationRead:
    integration = await _get_integration_for_user(db, user, integration_id)
    if body.name is not None:
        integration.name = body.name.strip()
    if body.is_active is not None:
        integration.is_active = body.is_active
    await record_audit(
        db,
        action="update_integration",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=integration.tenant_id,
    )
    await db.commit()
    return await IntegrationRepository(db).get(integration.id)

@router.post("/{integration_id}/sync", response_model=SyncExecutionRead)
async def trigger_sync(
    integration_id: UUID,
    body: SyncTriggerRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> SyncExecutionRead:
    """Persiste e enfileira uma execução real por capability."""
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise FeatureUnavailableError(
            "Esta integração não possui connector de pull configurado."
        )

    repo = IntegrationRepository(db)
    config = await repo.get_api_config(integration)
    if not config:
        raise FeatureUnavailableError("Configure o connector antes de sincronizar.")

    sync = await repo.create_sync(
        integration.id, integration.tenant_id, body.entity, trigger="manual"
    )
    sync_id = sync.id
    await db.commit()

    enqueued = await enqueue_job(
        "run_sync_job",
        integration_id=str(integration.id),
        entity=body.entity,
        run_id=str(sync_id),
    )
    if not enqueued:
        await _mark_sync_failed(db, sync_id, "Não foi possível enfileirar a sincronização.")
        raise ServiceUnavailableError("Não foi possível enfileirar a sincronização.")

    await record_audit(
        db,
        action="trigger_sync",
        entity="integration",
        entity_id=integration.id,
        user_id=user.id,
        tenant_id=integration.tenant_id,
    )
    await db.commit()
    return await repo.get_sync(sync_id)


@router.post(
    "/{integration_id}/sync-all",
    response_model=list[SyncExecutionRead],
    status_code=202,
)
async def trigger_full_sync(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> list[SyncExecutionRead]:
    """Agenda produtos e estoque como runs independentes e observáveis."""
    integration = await _get_integration_for_user(db, user, integration_id)
    if integration.type != "api":
        raise FeatureUnavailableError("Sync completa exige um connector de API.")
    repo = IntegrationRepository(db)
    if not await repo.get_api_config(integration):
        raise FeatureUnavailableError("Configure o connector antes de sincronizar.")
    correlation_id = str(uuid4())
    runs = [
        await repo.create_sync(
            integration.id,
            integration.tenant_id,
            entity,
            trigger="manual_full",
            correlation_id=correlation_id,
        )
        for entity in ("products", "stock")
    ]
    # O dispatcher genérico não pode executar estoque antes do catálogo.
    runs[1].next_retry_at = datetime.now(timezone.utc) + timedelta(hours=6)
    await db.commit()
    enqueued = await enqueue_job(
        "run_full_sync_job",
        integration_id=str(integration.id),
        product_run_id=str(runs[0].id),
        stock_run_id=str(runs[1].id),
    )
    if not enqueued:
        for run in runs:
            await _mark_sync_failed(db, run.id, "Não foi possível enfileirar a sincronização.")
    return [await repo.get_sync(run.id) for run in runs]

@router.get("/{integration_id}/syncs", response_model=list[SyncExecutionRead])
async def list_syncs(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_READ)),
) -> list[SyncExecutionRead]:
    """Histórico de execuções (seção 33: status, processed, errors, mensagem)."""
    await _get_integration_for_user(db, user, integration_id)
    repo = IntegrationRepository(db)
    return await repo.list_syncs(integration_id)


@router.get("/{integration_id}/runs", response_model=SyncExecutionPage)
async def list_runs_paginated(
    integration_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_READ)),
) -> SyncExecutionPage:
    await _get_integration_for_user(db, user, integration_id)
    repo = IntegrationRepository(db)
    total = await repo.count_syncs(integration_id)
    items = await repo.list_syncs(integration_id, page=page, page_size=page_size)
    return SyncExecutionPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get(
    "/{integration_id}/runs/{sync_id}", response_model=SyncExecutionRead
)
async def get_run_detail(
    integration_id: UUID,
    sync_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_READ)),
) -> SyncExecutionRead:
    integration = await _get_integration_for_user(db, user, integration_id)
    run = await IntegrationRepository(db).get_sync(sync_id)
    if run is None or run.integration_id != integration.id:
        raise NotFoundError("Execução não encontrada.")
    return run


@router.post(
    "/{integration_id}/syncs/{sync_id}/replay",
    response_model=SyncExecutionRead,
    status_code=202,
)
async def replay_sync(
    integration_id: UUID,
    sync_id: UUID,
    body: ReplayRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> SyncExecutionRead:
    """Cria outra execução; nunca reutiliza nem apaga o estado terminal original."""
    integration = await _get_integration_for_user(db, user, integration_id)
    repo = IntegrationRepository(db)
    source = await repo.get_sync(sync_id)
    terminal = {"success", "partial", "failed", "dead_letter"}
    source_status = getattr(source.status, "value", source.status) if source else None
    if source is None or source.integration_id != integration.id:
        raise NotFoundError("Execução não encontrada.")
    if source_status not in terminal:
        raise ValidationFailedError("Somente uma execução terminal pode ser repetida.")

    entity = source.entity
    replay = await repo.create_sync(
        integration.id,
        integration.tenant_id,
        entity,
        replay_of_id=source.id,
        trigger="replay",
        correlation_id=str(source.id),
    )
    stored_file = await repo.get_import_file_by_run(source.id)
    if stored_file:
        if stored_file.content is None:
            raise ValidationFailedError(
                "O payload deste arquivo já expirou pela política de retenção."
            )
        await repo.create_import_file(
            integration=integration,
            run_id=replay.id,
            filename=stored_file.filename,
            content_type=stored_file.content_type,
            content=stored_file.content,
        )
        job = "process_stock_file_job"
        kwargs = {"run_id": str(replay.id)}
    else:
        if integration.type != "api":
            raise ValidationFailedError(
                "Esta execução não possui arquivo ou connector reaplicável."
            )
        job = "run_sync_job"
        kwargs = {
            "integration_id": str(integration.id),
            "entity": entity,
            "run_id": str(replay.id),
        }
    replay.message = f"Replay de {source.id}." + (
        f" Motivo: {body.reason}" if body.reason else ""
    )
    await db.commit()
    enqueued = await enqueue_job(job, **kwargs)
    if not enqueued and job == "run_sync_job":
        await _mark_sync_failed(db, replay.id, "Não foi possível enfileirar o replay.")
        raise ServiceUnavailableError("Não foi possível enfileirar o replay.")
    return await repo.get_sync(replay.id)


@router.post(
    "/{integration_id}/inbox/{inbox_id}/replay",
    response_model=InboxAccepted,
    status_code=202,
)
async def replay_inbox(
    integration_id: UUID,
    inbox_id: UUID,
    body: ReplayRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_RUN)),
) -> InboxAccepted:
    """Reenvia uma mensagem terminal mantendo a cadeia de auditoria."""
    integration = await _get_integration_for_user(db, user, integration_id)
    repo = IntegrationRepository(db)
    source = await repo.get_inbox(inbox_id)
    if source is None or source.integration_id != integration.id:
        raise NotFoundError("Mensagem não encontrada.")
    if source.status not in {"succeeded", "dead_letter"}:
        raise ValidationFailedError("Somente uma mensagem terminal pode ser repetida.")
    if source.payload is None:
        raise ValidationFailedError(
            "O payload desta mensagem já expirou pela política de retenção."
        )
    replay_key = f"replay:{source.id}:{uuid4()}"
    replay, _ = await repo.create_or_get_inbox(
        integration=integration,
        channel=source.channel,
        capability=source.capability,
        idempotency_key=replay_key,
        payload=dict(source.payload),
        replay_of_id=source.id,
    )
    event_id = None
    if source.channel == "webhook":
        event = await repo.create_webhook_event(
            integration.id,
            integration.tenant_id,
            dict(source.payload),
            replay_key,
            inbox_id=replay.id,
        )
        event_id = event.id
        event_name = str(source.payload.get("event") or "unknown")
        run = await repo.create_sync(
            integration.id,
            integration.tenant_id,
            {
                "stock.sync": "stock",
                "product.sync": "products",
                "financial.sync": "financial",
            }.get(event_name, "unknown"),
            replay_of_id=source.run_id,
            trigger="replay",
            correlation_id=str(source.id),
        )
        replay.run_id = run.id
    elif source.channel == "agent":
        run = await repo.create_sync(
            integration.id,
            integration.tenant_id,
            "stock",
            replay_of_id=source.run_id,
            trigger="replay",
            correlation_id=str(source.id),
        )
        replay.run_id = run.id
    else:
        raise ValidationFailedError("Canal não suporta replay.")
    await db.commit()
    await enqueue_job("process_inbox_job", inbox_id=str(replay.id))
    return InboxAccepted(
        status="accepted", event_id=event_id, inbox_id=replay.id
    )

@router.get("/{integration_id}/webhook-events", response_model=list[WebhookEventRead])
async def list_webhook_events(
    integration_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(INTEGRATION_READ)),
) -> list[WebhookEventRead]:
    """Eventos de webhook recebidos (seção 31)."""
    await _get_integration_for_user(db, user, integration_id)
    repo = IntegrationRepository(db)
    return await repo.list_webhook_events(integration_id)
