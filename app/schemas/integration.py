"""Schemas de integrações (Bloco 11 — seções 28-33).

BLOCO I1: ingestão de estoque empurrada pelo AGENTE do cliente (push).
BLOCO B2: ingestão de estoque por ARQUIVO (CSV/Excel) — tipo `file`.
BLOCO B3: ingestão de estoque por WEBHOOK (evento stock.sync) — tipo `webhook`.
BLOCO B4: ingestão por PULL da API REST do ERP do cliente — tipo `api`.
- `stock` é SEMPRE inteiro: aceita 15, "15", "15.000" (→ 15) e "15,5" (→ 15).
  Nunca persiste "10.000" (o valor é truncado para int).
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.integrations.contracts import MAX_STOCK_VALUE, StockUpdate
from app.models.enums import SyncStatus, WebhookStatus

# Anti-DoS: teto de itens por lote enviado pelo agente (push).
MAX_STOCK_BATCH = 2000

# Anti-DoS: teto de linhas por arquivo importado (CSV/Excel) — Bloco B2.
MAX_IMPORT_ROWS = 20_000

class ERPIntegrationCreate(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    type: Literal["agent", "api", "webhook", "file"] = "agent"


class ERPIntegrationUpdate(BaseModel):
    name: str | None = Field(None, min_length=3, max_length=150)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if self.name is None and self.is_active is None:
            raise ValueError("Informe nome ou status para atualizar a integração.")
        return self

class ERPIntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    type: str
    is_active: bool
    created_at: datetime

class SyncTriggerRequest(BaseModel):
    entity: Literal["products", "stock"] = "products"

class SyncExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    integration_id: UUID
    entity: str
    status: SyncStatus
    processed: int
    errors: int
    started_at: datetime | None
    finished_at: datetime | None
    message: str | None
    attempt_count: int = 0
    max_attempts: int = 5
    next_retry_at: datetime | None = None
    terminal_at: datetime | None = None
    replay_of_id: UUID | None = None
    trigger: str = "legacy"
    correlation_id: str | None = None
    items_received: int = 0
    created_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    stale_count: int = 0
    skipped_count: int = 0
    item_errors: list[dict] | None = None
    item_errors_truncated: int = 0
    duration_ms: int | None = None
    error_code: str | None = None
    error_class: str | None = None
    retryable: bool | None = None
    last_attempt_at: datetime | None = None
    request_size_bytes: int | None = None
    created_at: datetime


class SyncExecutionPage(BaseModel):
    items: list[SyncExecutionRead]
    total: int
    page: int
    page_size: int
    pages: int


class IntegrationAlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    integration_id: UUID
    kind: str
    status: str
    severity: str
    message: str
    consecutive_failures: int
    opened_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    last_run_id: UUID | None


class IntegrationDashboardItem(BaseModel):
    integration_id: UUID
    name: str
    type: str
    is_active: bool
    health: Literal["healthy", "warning", "failing", "never_run"]
    last_status: str | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    average_duration_ms: int | None = None
    pending_runs: int = 0
    open_alerts: int = 0


class IntegrationDashboardRead(BaseModel):
    generated_at: datetime
    integrations: list[IntegrationDashboardItem]
    alerts: list[IntegrationAlertRead]

class WebhookEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    integration_id: UUID
    status: WebhookStatus
    received_at: datetime
    processed_at: datetime | None
    error: str | None
    created_at: datetime

# ---------- BLOCO I1 — Ingestão de estoque (push do agente) ----------
class StockItem(StockUpdate):
    """Um item de estoque vindo do ERP do cliente.

    DTO compatível da API. A validação efetiva pertence ao contrato canônico
    `StockUpdate`, compartilhado pelos quatro canais de entrada.

    - `sku`: chave de correlação com o catálogo do portal (obrigatório).
    - `stock`: quantidade INTEIRA (≥ 0) — normalizada pelo validator abaixo.
    - `external_id`: id do item no ERP (opcional, só para rastreio/depuração).
    """

class StockSyncRequest(BaseModel):
    """Lote de estoque empurrado pelo agente do cliente.

    - `batch_id`: chave de idempotência do LOTE. Se o agente repetir o envio
      (retry/timeout), o lote é ignorado — nunca reaplica o mesmo estoque.
    - `items`: itens do lote (máx. `MAX_STOCK_BATCH` — anti-DoS).
    """

    batch_id: str | None = Field(None, max_length=120)
    items: list[StockItem] = Field(..., min_length=1, max_length=MAX_STOCK_BATCH)

class StockSyncResult(BaseModel):
    """Resposta da ingestão — o agente/importador usa isso para log/alerta.

    ✅ Reutilizada pelos Blocos B2/B3/B4: o resultado de um arquivo, webhook
    ou pull tem exatamente a mesma forma do resultado de um push do agente.
    """

    sync_id: UUID
    status: str  # ok | partial | failed | duplicate
    processed: int
    unchanged: int
    stale: int = 0
    errors: int
    message: str | None = None
    details: list[dict] = Field(default_factory=list)


class InboxAccepted(BaseModel):
    status: Literal["accepted", "duplicate"]
    event_id: UUID | None = None
    inbox_id: UUID


class ReplayRequest(BaseModel):
    reason: str | None = Field(None, max_length=300)

# ---------- BLOCO I1 — Chave de API do agente (admin do tenant) ----------
class AgentApiKeyCreated(BaseModel):
    """Resposta da EMISSÃO — a chave aparece UMA única vez."""

    prefix: str
    api_key: str

class AgentApiKeyRead(BaseModel):
    """Status da chave (a chave em claro nunca é devolvida)."""

    prefix: str | None = None
    is_active: bool

class WebhookSecretCreated(BaseModel):
    secret: str
    rotated_at: datetime
    previous_valid_until: datetime | None = None

class WebhookSecretRead(BaseModel):
    configured: bool
    rotated_at: datetime | None = None
    previous_valid_until: datetime | None = None

# ---------- BLOCO B4 — Configuração do PULL (tipo `api`) ----------
class ApiPullConfigIn(BaseModel):
    """Configuração do conector de pull da API REST do ERP do cliente.

    - `base_url`: base da API (http/https).
    - `path`: caminho do endpoint que retorna o estoque.
    - `auth_type`: none | bearer | basic.
    - `token`/`username`/`password`: credenciais (CIFRADAS no armazenamento).
    - `data_path`: caminho (separado por '.') até o ARRAY de itens no JSON.
      Vazio = o próprio corpo da resposta é o array.
    - `sku_field`/`stock_field`/`external_id_field`: nomes dos campos em cada item.
    - `interval_minutes`: frequência do pull (1 a 1440 min).
    - `headers`: cabeçalhos adicionais (valores CIFRADOS no armazenamento).
    """

    base_url: str = Field(..., min_length=8, max_length=500)
    path: str = Field("/", max_length=500)
    auth_type: Literal["none", "bearer", "basic"] = "none"
    token: str | None = Field(None, max_length=2000)
    username: str | None = Field(None, max_length=200)
    password: str | None = Field(None, max_length=2000)
    data_path: str = Field("", max_length=200)
    sku_field: str = Field("sku", min_length=1, max_length=80)
    stock_field: str = Field("stock", min_length=1, max_length=80)
    external_id_field: str = Field("external_id", max_length=80)
    occurred_at_field: str = Field("", max_length=80)
    source_version_field: str = Field("", max_length=80)
    cursor_param: str = Field("", max_length=80)
    next_cursor_path: str = Field("", max_length=200)
    product_fields: dict[str, str] = Field(default_factory=dict)
    interval_minutes: int = Field(15, ge=1, le=1440)
    headers: dict[str, str] = Field(default_factory=dict)
    token_mode: Literal["keep", "replace", "clear"] = "keep"
    username_mode: Literal["keep", "replace", "clear"] = "keep"
    password_mode: Literal["keep", "replace", "clear"] = "keep"
    headers_mode: Literal["keep", "replace", "clear"] = "keep"

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v):
        v = v.strip()
        if not v.startswith("https://"):
            raise ValueError("base_url deve usar HTTPS.")
        return v

    @model_validator(mode="after")
    def _validate_auth_consistency(self):
        """Garante que as credenciais exigidas pelo auth_type estão presentes.

        ✅ model_validator(mode='after'): roda com o modelo COMPLETO, então
        enxerga token/username/password (um field_validator em 'auth_type'
        não os veria, pois vêm depois no modelo).
        """
        if self.token_mode == "replace" and not (self.token or "").strip():
            raise ValueError("token_mode replace exige token.")
        if self.username_mode == "replace" and not (self.username or "").strip():
            raise ValueError("username_mode replace exige username.")
        if self.password_mode == "replace" and not (self.password or "").strip():
            raise ValueError("password_mode replace exige password.")
        return self

    @field_validator("product_fields")
    @classmethod
    def _validate_product_fields(cls, value):
        allowed = {
            "sku", "external_id", "code", "name", "description", "brand",
            "category_external_id", "category_name", "unit", "price", "stock",
            "image_url", "status", "ean", "attributes", "variations", "weight",
            "width", "height", "length", "occurred_at",
        }
        if len(value) > len(allowed):
            raise ValueError("Mapeamento de produtos excede o limite permitido.")
        clean = {}
        for target, source in value.items():
            if target not in allowed:
                raise ValueError(f"Campo canônico não permitido: {target}.")
            path = source.strip()
            if not path or len(path) > 200:
                raise ValueError(f"Caminho inválido para {target}.")
            clean[target] = path
        return clean

class ApiPullConfigRead(BaseModel):
    """Configuração do pull para leitura — segredos NUNCA são devolvidos."""

    base_url: str
    path: str
    auth_type: str
    data_path: str
    sku_field: str
    stock_field: str
    external_id_field: str
    occurred_at_field: str = ""
    source_version_field: str = ""
    cursor_param: str = ""
    next_cursor_path: str = ""
    product_fields: dict[str, str] = Field(default_factory=dict)
    interval_minutes: int
    token_set: bool
    username_set: bool
    password_set: bool
    header_keys: list[str] = Field(default_factory=list)

class ApiPullTestResult(BaseModel):
    """Resultado do teste de conexão (sem aplicar nada no banco)."""

    ok: bool
    status_code: int | None = None
    items_found: int = 0
    message: str


class IntegrationDryRunRequest(BaseModel):
    entity: Literal["products", "stock"] = "stock"
    config: ApiPullConfigIn | None = None
    sample_size: int = Field(10, ge=1, le=20)


class IntegrationDryRunResult(BaseModel):
    ok: bool
    entity: str
    received: int = 0
    valid: int = 0
    errors: int = 0
    message: str
    sample: list[dict] = Field(default_factory=list)
    details: list[dict] = Field(default_factory=list)
