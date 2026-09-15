"""Repositório de integrações (Bloco 11 — seções 28-33).

- ERPIntegration, SyncExecution e WebhookEvent.
- Consultas de administração filtram por tenant (isolamento, seção 5).
- Webhook usa tenant_id explícito (chamada externa, sem sessão).
- SyncExecution e WebhookEvent herdam TenantMixin (tenant_id NOT NULL) —
  por isso o tenant_id é SEMPRE preenchido aqui, nunca None.

✅ BLOCO I1 — chave de API do AGENTE de integração:
- A chave (hash SHA-256) é gravada em `ERPIntegration.config_encrypted`
  no formato "PREFIXO:HASH" — sem migração e sem segredo em claro.
- A autenticação do agente busca a integração por IGUALDADE EXATA do
  registro reconstruído a partir da chave apresentada (sem LIKE).

✅ BLOCO B4 — config do PULL (tipo `api`):
- A configuração da API do cliente (JSON, com segredos cifrados) também
  fica em `config_encrypted`. Não conflita com a chave do agente: uma
  integração tem UM tipo, e o formato "PREFIXO:HASH" nunca é um JSON.
"""
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_keys import build_api_key_record, record_prefix
from app.models import ERPIntegration, SyncExecution, WebhookEvent
from app.models.enums import WebhookStatus

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
        integration.config_encrypted = build_api_key_record(raw_key)
        await self.db.flush()

    async def get_agent_api_key_prefix(self, integration: ERPIntegration) -> str | None:
        """Prefixo da chave ativa (para exibir na UI) ou None se não houver."""
        return record_prefix(integration.config_encrypted)

    async def clear_agent_api_key(self, integration: ERPIntegration) -> None:
        """Revoga a chave do agente (remove o registro)."""
        integration.config_encrypted = None
        await self.db.flush()

    async def get_by_agent_api_key(self, raw_key: str) -> ERPIntegration | None:
        """Resolve a integração dona da chave apresentada pelo agente.

        Busca por igualdade exata do registro "PREFIXO:HASH" — o tenant sai
        SEMPRE daqui (nunca do payload), garantindo o isolamento multi-tenant.
        """
        record = build_api_key_record(raw_key)
        result = await self.db.execute(
            select(ERPIntegration).where(
                ERPIntegration.config_encrypted == record
            )
        )
        return result.scalars().first()

    # ---------- Config do PULL (Bloco B4) ----------
    async def get_api_config(self, integration: ERPIntegration) -> dict | None:
        """Lê a config JSON do pull (tipo `api`) ou None se não existir."""
        raw = integration.config_encrypted
        if not raw:
            return None
        try:
            cfg = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return cfg if isinstance(cfg, dict) else None

    async def set_api_config(self, integration: ERPIntegration, config: dict) -> None:
        """Grava a config JSON do pull (segredos já cifrados pelo chamador)."""
        integration.config_encrypted = json.dumps(config)
        await self.db.flush()

    # ---------- Sync executions (seção 33) ----------
    async def create_sync(
        self, integration_id: UUID, tenant_id: UUID, entity: str
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
        )
        self.db.add(sync)
        await self.db.flush()
        return sync

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

    # ---------- Webhook events (seção 31) ----------
    async def create_webhook_event(
        self, integration_id: UUID, tenant_id: UUID, payload: dict
    ) -> WebhookEvent:
        """Registra o evento recebido com o tenant da integração.

        CORRIGIDO: tenant_id era None e violava a constraint NOT NULL
        do TenantMixin (causa do 500 no 11.8). Agora é obrigatório.
        """
        event = WebhookEvent(
            tenant_id=tenant_id,
            integration_id=integration_id,
            payload=payload,
            status=WebhookStatus.RECEIVED,
            received_at=datetime.now(timezone.utc),
        )
        self.db.add(event)
        await self.db.flush()
        return event

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