"""Repositório de integrações (Bloco 11 — seções 28-33).

- ERPIntegration, SyncExecution e WebhookEvent.
- Consultas de administração filtram por tenant (isolamento, seção 5).
- Webhook usa tenant_id explícito (chamada externa, sem sessão).
- SyncExecution e WebhookEvent herdam TenantMixin (tenant_id NOT NULL) —
  por isso o tenant_id é SEMPRE preenchido aqui, nunca None.

✅ BLOCO I1 — chave de API do AGENTE de integração:
- Prefixo e hash SHA-256 possuem colunas próprias; a chave nunca é persistida.
- A autenticação busca por igualdade do hash, sem LIKE.

✅ BLOCO B4 — config do PULL (tipo `api`):
- A configuração JSON com segredos cifrados possui coluna separada.
"""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_keys import api_key_prefix, hash_api_key
from app.models import (
    ERPIntegration,
    IntegrationApiKey,
    IntegrationAlert,
    IntegrationConfiguration,
    IntegrationCredential,
    IntegrationImportFile,
    IntegrationInbox,
    IntegrationSchedule,
    SyncExecution,
    WebhookEvent,
)
from app.models.enums import WebhookStatus

API_CREDENTIAL_KIND = "api"
WEBHOOK_CREDENTIAL_KIND = "webhook"
_API_SECRET_FIELDS = {"token", "username", "password", "headers"}
_CONNECTOR_FIELD = "_connector"

class IntegrationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------- Integrações ----------
    async def get(self, integration_id: UUID) -> ERPIntegration | None:
        result = await self.db.execute(
            select(ERPIntegration).where(ERPIntegration.id == integration_id)
        )
        return result.scalars().first()

    async def list_for_tenant(self, tenant_id: UUID) -> list[ERPIntegration]:
        result = await self.db.execute(
            select(ERPIntegration)
            .where(ERPIntegration.tenant_id == tenant_id)
            .order_by(ERPIntegration.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_active(self) -> list[ERPIntegration]:
        """Para o worker/cronjob: integrações ativas de todos os tenants."""
        result = await self.db.execute(
            select(ERPIntegration).where(ERPIntegration.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def list_active_by_type(self, type_: str) -> list[ERPIntegration]:
        """Integrações ativas de um tipo (ex.: 'api') — para o cron de pull."""
        result = await self.db.execute(
            select(ERPIntegration).where(
                ERPIntegration.is_active.is_(True),
                ERPIntegration.type == type_,
            )
        )
        return list(result.scalars().all())

    async def create(
        self, tenant_id: UUID, name: str, type_: str
    ) -> ERPIntegration:
        integration = ERPIntegration(
            tenant_id=tenant_id, name=name, type=type_, is_active=True
        )
        self.db.add(integration)
        await self.db.flush()
        return integration

    # ---------- Chave de API do agente (Bloco I1) ----------
    async def set_agent_api_key(self, integration: ERPIntegration, raw_key: str) -> None:
        """Grava (ou ROTACIONA) a chave do agente: substitui pelo novo hash.

        A chave em claro nunca entra no banco — apenas "PREFIXO:HASH".
        """
        result = await self.db.execute(
            select(IntegrationApiKey).where(
                IntegrationApiKey.integration_id == integration.id,
                IntegrationApiKey.tenant_id == integration.tenant_id,
            )
        )
        api_key = result.scalars().first()
        if api_key is None:
            api_key = IntegrationApiKey(
                integration_id=integration.id,
                tenant_id=integration.tenant_id,
                key_hash=hash_api_key(raw_key),
                key_prefix=api_key_prefix(raw_key),
                rotated_at=datetime.now(timezone.utc),
            )
            self.db.add(api_key)
        else:
            api_key.key_hash = hash_api_key(raw_key)
            api_key.key_prefix = api_key_prefix(raw_key)
            api_key.rotated_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def get_agent_api_key_prefix(self, integration: ERPIntegration) -> str | None:
        """Prefixo da chave ativa (para exibir na UI) ou None se não houver."""
        result = await self.db.execute(
            select(IntegrationApiKey.key_prefix).where(
                IntegrationApiKey.integration_id == integration.id,
                IntegrationApiKey.tenant_id == integration.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def clear_agent_api_key(self, integration: ERPIntegration) -> None:
        """Revoga a chave do agente (remove o registro)."""
        await self.db.execute(
            delete(IntegrationApiKey).where(
                IntegrationApiKey.integration_id == integration.id,
                IntegrationApiKey.tenant_id == integration.tenant_id,
            )
        )
        await self.db.flush()

    async def get_by_agent_api_key(self, raw_key: str) -> ERPIntegration | None:
        """Resolve a integração dona da chave apresentada pelo agente.

        Busca por igualdade exata do registro "PREFIXO:HASH" — o tenant sai
        SEMPRE daqui (nunca do payload), garantindo o isolamento multi-tenant.
        """
        result = await self.db.execute(
            select(ERPIntegration)
            .join(
                IntegrationApiKey,
                IntegrationApiKey.integration_id == ERPIntegration.id,
            )
            .where(IntegrationApiKey.key_hash == hash_api_key(raw_key))
        )
        return result.scalars().first()

    # ---------- Config do PULL (Bloco B4) ----------
    async def get_api_config(self, integration: ERPIntegration) -> dict | None:
        """Compõe config operacional e credenciais cifradas para o connector."""
        result = await self.db.execute(
            select(IntegrationConfiguration).where(
                IntegrationConfiguration.integration_id == integration.id,
                IntegrationConfiguration.tenant_id == integration.tenant_id,
            )
        )
        configuration = result.scalars().first()
        if configuration is None:
            return None
        credential = await self.get_credential(integration, API_CREDENTIAL_KIND)
        return {
            _CONNECTOR_FIELD: configuration.connector,
            **dict(configuration.settings or {}),
            **dict(credential.payload if credential else {}),
        }

    async def set_api_config(self, integration: ERPIntegration, config: dict) -> None:
        """Persiste configuração e credenciais em entidades independentes."""
        connector = str(config.get(_CONNECTOR_FIELD) or "rest_json")
        settings = {
            key: value
            for key, value in config.items()
            if key not in _API_SECRET_FIELDS and key != _CONNECTOR_FIELD
        }
        secrets = {
            key: config[key] for key in _API_SECRET_FIELDS if key in config
        }

        result = await self.db.execute(
            select(IntegrationConfiguration).where(
                IntegrationConfiguration.integration_id == integration.id,
                IntegrationConfiguration.tenant_id == integration.tenant_id,
            )
        )
        configuration = result.scalars().first()
        if configuration is None:
            configuration = IntegrationConfiguration(
                integration_id=integration.id,
                tenant_id=integration.tenant_id,
                connector=connector,
                settings=settings,
            )
            self.db.add(configuration)
        else:
            configuration.connector = connector
            configuration.settings = settings

        credential = await self.get_credential(integration, API_CREDENTIAL_KIND)
        if credential is None:
            credential = IntegrationCredential(
                integration_id=integration.id,
                tenant_id=integration.tenant_id,
                kind=API_CREDENTIAL_KIND,
                payload=secrets,
                rotated_at=datetime.now(timezone.utc),
            )
            self.db.add(credential)
        else:
            credential.payload = secrets
            credential.rotated_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def get_credential(
        self, integration: ERPIntegration, kind: str
    ) -> IntegrationCredential | None:
        result = await self.db.execute(
            select(IntegrationCredential).where(
                IntegrationCredential.integration_id == integration.id,
                IntegrationCredential.tenant_id == integration.tenant_id,
                IntegrationCredential.kind == kind,
            )
        )
        return result.scalars().first()

    async def get_webhook_credential(
        self, integration: ERPIntegration
    ) -> IntegrationCredential | None:
        return await self.get_credential(integration, WEBHOOK_CREDENTIAL_KIND)

    # ---------- Segredo de webhook ----------
    async def rotate_webhook_secret(
        self,
        integration: ERPIntegration,
        *,
        encrypted_secret: str,
        rotated_at: datetime,
    ) -> None:
        credential = await self.get_webhook_credential(integration)
        payload = {"secret": encrypted_secret}
        if credential is None:
            credential = IntegrationCredential(
                integration_id=integration.id,
                tenant_id=integration.tenant_id,
                kind=WEBHOOK_CREDENTIAL_KIND,
                payload=payload,
                rotated_at=rotated_at,
            )
            self.db.add(credential)
        else:
            credential.previous_payload = dict(credential.payload or {}) or None
            credential.payload = payload
            credential.rotated_at = rotated_at
        await self.db.flush()

    # ---------- Sync executions (seção 33) ----------
    async def create_sync(
        self,
        integration_id: UUID,
        tenant_id: UUID,
        entity: str,
        *,
        replay_of_id: UUID | None = None,
        trigger: str = "internal",
        correlation_id: str | None = None,
        request_size_bytes: int | None = None,
        run_metadata: dict | None = None,
    ) -> SyncExecution:
        """Registra uma execução de sincronização com o tenant da integração.

        CORRIGIDO: tenant_id era None e violava a constraint NOT NULL
        do TenantMixin (causa do 500 no webhook). Agora é obrigatório.
        """
        sync = SyncExecution(
            tenant_id=tenant_id,
            integration_id=integration_id,
            entity=entity,
            status="pending",
            replay_of_id=replay_of_id,
            trigger=trigger,
            correlation_id=correlation_id,
            request_size_bytes=request_size_bytes,
            run_metadata=run_metadata,
        )
        self.db.add(sync)
        await self.db.flush()
        return sync

    async def get_sync(
        self, sync_id: UUID, *, for_update: bool = False
    ) -> SyncExecution | None:
        statement = select(SyncExecution).where(SyncExecution.id == sync_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalars().first()

    async def list_syncs(
        self, integration_id: UUID, page: int = 1, page_size: int = 20
    ) -> list[SyncExecution]:
        result = await self.db.execute(
            select(SyncExecution)
            .where(SyncExecution.integration_id == integration_id)
            .order_by(SyncExecution.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all())

    async def count_syncs(self, integration_id: UUID) -> int:
        result = await self.db.execute(
            select(func.count(SyncExecution.id)).where(
                SyncExecution.integration_id == integration_id
            )
        )
        return int(result.scalar_one())

    async def list_alerts(
        self, tenant_id: UUID, *, open_only: bool = True
    ) -> list[IntegrationAlert]:
        statement = select(IntegrationAlert).where(
            IntegrationAlert.tenant_id == tenant_id
        )
        if open_only:
            statement = statement.where(IntegrationAlert.status == "open")
        result = await self.db.execute(
            statement.order_by(
                IntegrationAlert.severity.desc(),
                IntegrationAlert.last_seen_at.desc(),
            )
        )
        return list(result.scalars().all())

    # ---------- Inbox transacional / replay ----------
    @staticmethod
    def payload_hash(payload: dict) -> str:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    async def create_or_get_inbox(
        self,
        *,
        integration: ERPIntegration,
        channel: str,
        capability: str,
        idempotency_key: str,
        payload: dict,
        run_id: UUID | None = None,
        replay_of_id: UUID | None = None,
    ) -> tuple[IntegrationInbox, bool]:
        """Insere por chave única na mesma transação, sem janela de corrida."""
        digest = self.payload_hash(payload)
        statement = (
            pg_insert(IntegrationInbox)
            .values(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                channel=channel,
                capability=capability,
                idempotency_key=idempotency_key,
                payload_hash=digest,
                payload=payload,
                run_id=run_id,
                replay_of_id=replay_of_id,
            )
            .on_conflict_do_nothing(
                index_elements=["integration_id", "channel", "idempotency_key"]
            )
            .returning(IntegrationInbox.id)
        )
        inserted_id = (await self.db.execute(statement)).scalar_one_or_none()
        if inserted_id is not None:
            item = await self.get_inbox(inserted_id)
            assert item is not None
            return item, True
        result = await self.db.execute(
            select(IntegrationInbox).where(
                IntegrationInbox.integration_id == integration.id,
                IntegrationInbox.channel == channel,
                IntegrationInbox.idempotency_key == idempotency_key,
            )
        )
        item = result.scalars().one()
        if item.payload_hash != digest:
            raise ValueError("Chave de idempotência já usada com outro payload.")
        return item, False

    async def get_inbox(
        self, inbox_id: UUID, *, for_update: bool = False
    ) -> IntegrationInbox | None:
        statement = select(IntegrationInbox).where(IntegrationInbox.id == inbox_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalars().first()

    async def claim_due_inbox(self, limit: int = 100) -> list[IntegrationInbox]:
        result = await self.db.execute(
            select(IntegrationInbox)
            .where(
                IntegrationInbox.status.in_(("pending", "retry")),
                IntegrationInbox.available_at <= datetime.now(timezone.utc),
            )
            .order_by(IntegrationInbox.available_at, IntegrationInbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    # ---------- Arquivos assíncronos ----------
    async def create_import_file(
        self,
        *,
        integration: ERPIntegration,
        run_id: UUID,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> IntegrationImportFile:
        item = IntegrationImportFile(
            tenant_id=integration.tenant_id,
            integration_id=integration.id,
            run_id=run_id,
            filename=filename[:255],
            content_type=(content_type or "")[:120] or None,
            content=content,
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
        )
        self.db.add(item)
        await self.db.flush()
        return item

    async def get_import_file_by_run(
        self, run_id: UUID, *, for_update: bool = False
    ) -> IntegrationImportFile | None:
        statement = select(IntegrationImportFile).where(
            IntegrationImportFile.run_id == run_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalars().first()

    # ---------- Scheduler persistido ----------
    async def get_schedule(
        self, integration_id: UUID, capability: str
    ) -> IntegrationSchedule | None:
        result = await self.db.execute(
            select(IntegrationSchedule).where(
                IntegrationSchedule.integration_id == integration_id,
                IntegrationSchedule.capability == capability,
            )
        )
        return result.scalars().first()

    async def get_schedule_by_id(self, schedule_id: UUID) -> IntegrationSchedule | None:
        result = await self.db.execute(
            select(IntegrationSchedule).where(IntegrationSchedule.id == schedule_id)
        )
        return result.scalars().first()

    async def upsert_schedule(
        self,
        *,
        integration: ERPIntegration,
        capability: str,
        interval_seconds: int,
        jitter_seconds: int,
        max_batch_size: int,
        next_run_at: datetime | None = None,
    ) -> IntegrationSchedule:
        schedule = await self.get_schedule(integration.id, capability)
        if schedule is None:
            schedule = IntegrationSchedule(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                capability=capability,
                interval_seconds=interval_seconds,
                jitter_seconds=jitter_seconds,
                max_batch_size=max_batch_size,
                next_run_at=next_run_at or datetime.now(timezone.utc),
                is_active=integration.is_active,
            )
            self.db.add(schedule)
        else:
            schedule.interval_seconds = interval_seconds
            schedule.jitter_seconds = jitter_seconds
            schedule.max_batch_size = max_batch_size
            schedule.is_active = integration.is_active
            if next_run_at is not None:
                schedule.next_run_at = next_run_at
        await self.db.flush()
        return schedule

    async def claim_due_schedules(self, limit: int = 100) -> list[IntegrationSchedule]:
        result = await self.db.execute(
            select(IntegrationSchedule)
            .join(
                ERPIntegration,
                ERPIntegration.id == IntegrationSchedule.integration_id,
            )
            .where(
                IntegrationSchedule.is_active.is_(True),
                IntegrationSchedule.next_run_at <= datetime.now(timezone.utc),
                ERPIntegration.is_active.is_(True),
            )
            .order_by(IntegrationSchedule.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    # ---------- Webhook events (seção 31) ----------
    async def create_webhook_event(
        self,
        integration_id: UUID,
        tenant_id: UUID,
        payload: dict,
        idempotency_key: str,
        inbox_id: UUID | None = None,
    ) -> WebhookEvent:
        """Registra o evento recebido com o tenant da integração.

        CORRIGIDO: tenant_id era None e violava a constraint NOT NULL
        do TenantMixin (causa do 500 no 11.8). Agora é obrigatório.
        """
        event = WebhookEvent(
            tenant_id=tenant_id,
            integration_id=integration_id,
            payload=payload,
            idempotency_key=idempotency_key,
            status=WebhookStatus.RECEIVED,
            received_at=datetime.now(timezone.utc),
            inbox_id=inbox_id,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def get_webhook_event(self, event_id: UUID) -> WebhookEvent | None:
        result = await self.db.execute(
            select(WebhookEvent).where(WebhookEvent.id == event_id)
        )
        return result.scalars().first()

    async def get_webhook_event_by_idempotency(
        self,
        integration_id: UUID,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> WebhookEvent | None:
        statement = select(WebhookEvent).where(
            WebhookEvent.integration_id == integration_id,
            WebhookEvent.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalars().first()

    async def list_webhook_events(
        self, integration_id: UUID, page: int = 1, page_size: int = 20
    ) -> list[WebhookEvent]:
        result = await self.db.execute(
            select(WebhookEvent)
            .where(WebhookEvent.integration_id == integration_id)
            .order_by(WebhookEvent.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all())
