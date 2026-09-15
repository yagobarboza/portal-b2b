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

from app.models.enums import SyncStatus, WebhookStatus

# Anti-DoS: teto de itens por lote enviado pelo agente (push).
MAX_STOCK_BATCH = 2000

# Anti-DoS: teto de linhas por arquivo importado (CSV/Excel) — Bloco B2.
MAX_IMPORT_ROWS = 20_000

# Teto de sanidade por item (evita overflow/typo absurdo do ERP).
MAX_STOCK_VALUE = 1_000_000

class ERPIntegrationCreate(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    type: str = Field(default="erp", max_length=50)

class ERPIntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    type: str
    is_active: bool
    created_at: datetime

class SyncTriggerRequest(BaseModel):
    entity: str = Field(default="financial", max_length=50)

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
    created_at: datetime

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
class StockItem(BaseModel):
    """Um item de estoque vindo do ERP do cliente.

    - `sku`: chave de correlação com o catálogo do portal (obrigatório).
    - `stock`: quantidade INTEIRA (≥ 0) — normalizada pelo validator abaixo.
    - `external_id`: id do item no ERP (opcional, só para rastreio/depuração).
    """

    sku: str = Field(..., min_length=1, max_length=80)
    stock: int = Field(..., ge=0, le=MAX_STOCK_VALUE)
    external_id: str | None = Field(None, max_length=120)

    @field_validator("stock", mode="before")
    @classmethod
    def _normalize_stock(cls, v):
        """Converte qualquer entrada em inteiro (truncando decimais).

        Aceita: 15 | 15.0 | "15" | "15.000" (→ 15) | "15,5" (→ 15).
        Rejeita: vazio, booleano, negativo e texto não numérico.
        """
        if isinstance(v, bool):
            raise ValueError("Estoque inválido.")
        if isinstance(v, str):
            s = v.strip().replace(" ", "")
            if not s:
                raise ValueError("Estoque vazio.")
            s = s.replace(",", ".")
            try:
                n = float(s)
            except ValueError as exc:
                raise ValueError(f"Estoque inválido: {v!r}") from exc
        else:
            try:
                n = float(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Estoque inválido: {v!r}") from exc
        if n < 0:
            raise ValueError("Estoque não pode ser negativo.")
        if n > MAX_STOCK_VALUE:
            raise ValueError(f"Estoque acima do limite ({MAX_STOCK_VALUE}).")
        # ✅ Trunca: 15.000 → 15 (estoque é inteiro, nunca "10.000")
        return int(n)

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
    errors: int
    message: str | None = None
    details: list[dict] = Field(default_factory=list)

# ---------- BLOCO I1 — Chave de API do agente (admin do tenant) ----------
class AgentApiKeyCreated(BaseModel):
    """Resposta da EMISSÃO — a chave aparece UMA única vez."""

    prefix: str
    api_key: str

class AgentApiKeyRead(BaseModel):
    """Status da chave (a chave em claro nunca é devolvida)."""

    prefix: str | None = None
    is_active: bool

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
    interval_minutes: int = Field(15, ge=1, le=1440)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v):
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url deve começar com http:// ou https://.")
        return v

    @model_validator(mode="after")
    def _validate_auth_consistency(self):
        """Garante que as credenciais exigidas pelo auth_type estão presentes.

        ✅ model_validator(mode='after'): roda com o modelo COMPLETO, então
        enxerga token/username/password (um field_validator em 'auth_type'
        não os veria, pois vêm depois no modelo).
        """
        if self.auth_type == "bearer" and not (self.token or "").strip():
            raise ValueError("auth_type bearer exige token.")
        if self.auth_type == "basic" and not (
            (self.username or "").strip() and (self.password or "").strip()
        ):
            raise ValueError("auth_type basic exige username e password.")
        return self

class ApiPullConfigRead(BaseModel):
    """Configuração do pull para leitura — segredos NUNCA são devolvidos."""

    base_url: str
    path: str
    auth_type: str
    data_path: str
    sku_field: str
    stock_field: str
    external_id_field: str
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