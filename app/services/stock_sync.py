"""Ingestão de estoque — AGENTE (push), ARQUIVO (CSV/Excel) e WEBHOOK.

Três origens convergem para o MESMO motor de aplicação (`apply_stock_sync`):

1. AGENTE (Bloco I1, tipo `agent`) — push via chave de API:
   POST /integrations/agent/stock
2. ARQUIVO (Bloco B2, tipo `file`) — importação de CSV/Excel:
   POST /integrations/{integration_id}/stock/import
3. WEBHOOK (Bloco B3, tipo `webhook`) — evento `stock.sync` do ERP:
   POST /webhooks/{integration_id}  (assinatura HMAC-SHA256)

Garantias (comuns às três origens):
- Isolamento por tenant: o tenant vem da CHAVE (agente), da SESSÃO (arquivo)
  ou da INTEGRAÇÃO resolvida pela URL assinada (webhook).
- Idempotência: `batch_id` repetido é ignorado (Redis, fail-open).
- Estoque SEMPRE inteiro e ≥ 0 (truncado — nunca "10.000").
- Nunca cria produto: SKU inexistente vira erro tratado (catálogo curado).
- Cada execução fica registrada em SyncExecution (processed/errors/mensagem).
- N+1 evitado: os SKUs do lote são resolvidos em UMA query.
"""
import csv
import io
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Product, SyncExecution
from app.models.enums import SyncStatus
from app.schemas.integration import MAX_IMPORT_ROWS, StockItem

logger = logging.getLogger("stock_sync")

settings = get_settings()
_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

# Entidade registrada em SyncExecution para ingestão de estoque.
ENTITY = "stock"
# Teto de mensagens devolvidas ao importador (anti-payload gigante).
MAX_DETAILS = 50
# TTL da chave de idempotência do lote (segundos).
IDEMPOTENCY_TTL = int(getattr(settings, "STOCK_IDEMPOTENCY_TTL", 24 * 3600))

# Cabeçalhos aceitos no arquivo (case-insensitive) — Bloco B2.
SKU_HEADERS = ("sku", "codigo", "código", "codigo_sku")
STOCK_HEADERS = ("stock", "estoque", "quantidade", "qtd", "saldo")
EXTERNAL_ID_HEADERS = ("external_id", "id_externo")

# ==================== Idempotência e mensagens ====================
async def _claim_batch(tenant_id, batch_id: str) -> bool:
    """True se o lote é inédito. Fail-open: sem Redis, processa (não perde sync)."""
    try:
        key = f"idem:stock:{tenant_id}:{batch_id}"
        return bool(await _redis.set(key, "1", nx=True, ex=IDEMPOTENCY_TTL))
    except Exception:  # noqa: BLE001 — cache nunca derruba a ingestão
        logger.warning("Redis indisponível — idempotência de lote ignorada.")
        return True

def _message(processed: int, unchanged: int, errors: int) -> str:
    return (
        f"{processed} atualizado(s), {unchanged} inalterado(s), "
        f"{errors} erro(s)."
    )

def _first_error(exc: ValidationError) -> str:
    """Extrai a mensagem legível do erro de validação de um item."""
    try:
        msg = str(exc.errors()[0].get("msg", "Valor inválido."))
    except Exception:  # noqa: BLE001
        return "Valor inválido."
    return msg.replace("Value error, ", "")

# ==================== BLOCO B2 — Leitura do arquivo ====================
def _norm_key(value) -> str:
    """Normaliza o nome de uma coluna (case-insensitive, sem espaços)."""
    return str(value).strip().lower() if value is not None else ""

def _sniff_delimiter(sample: str) -> str:
    """Detecta ',' ou ';' no CSV (exportações pt-BR costumam usar ';')."""
    first = sample.splitlines()[0] if sample and sample.strip() else ""
    return ";" if first.count(";") > first.count(",") else ","

def iter_stock_rows(filename: str, content: bytes):
    """Gera linhas (dict) a partir de CSV ou Excel (.xlsx/.xlsm).

    - .xlsx/.xlsm: primeira planilha, primeira linha = cabeçalho.
    - Demais: interpretado como CSV (UTF-8 com BOM), com detecção de ';' ou ','.
    A chave de cada dict é o nome da coluna em minúsculas.
    """
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        headers = [_norm_key(h) for h in next(rows, ())]
        for values in rows:
            yield {h: ("" if v is None else v) for h, v in zip(headers, values) if h}
        wb.close()
        return

    text = content.decode("utf-8-sig")
    delimiter = _sniff_delimiter(text)
    for row in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        yield {
            _norm_key(k): ("" if v is None else v)
            for k, v in row.items()
            if k
        }

def _pick(row: dict, headers: tuple[str, ...]) -> str:
    """Retorna o primeiro valor não-vazio entre os aliases de cabeçalho."""
    for header in headers:
        value = row.get(header)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""

def parse_stock_rows(
    rows, max_rows: int = MAX_IMPORT_ROWS
) -> tuple[list[StockItem], list[dict]]:
    """Converte as linhas do arquivo em StockItem, separando os erros por linha.

    NUNCA levanta por linha inválida: cada problema vira um item em `errors`
    (com o número da linha do arquivo) e o restante continua sendo importado.
    A primeira linha (cabeçalho) corresponde à linha 1, então os dados
    começam na linha 2.
    """
    items: list[StockItem] = []
    errors: list[dict] = []
    data_rows = 0

    for line, row in enumerate(rows, start=2):
        data_rows += 1
        if data_rows > max_rows:
            errors.append(
                {"row": line, "error": f"Arquivo excede o limite de {max_rows} linhas."}
            )
            break

        sku = _pick(row, SKU_HEADERS)
        if not sku:
            errors.append({"row": line, "error": "Linha sem SKU."})
            continue

        raw_stock = _pick(row, STOCK_HEADERS)
        if raw_stock == "":
            errors.append({"row": line, "sku": sku, "error": "Linha sem valor de estoque."})
            continue

        try:
            item = StockItem.model_validate(
                {
                    "sku": sku,
                    "stock": raw_stock,
                    "external_id": _pick(row, EXTERNAL_ID_HEADERS) or None,
                }
            )
        except ValidationError as exc:
            errors.append({"row": line, "sku": sku, "error": _first_error(exc)})
            continue

        items.append(item)

    if data_rows == 0:
        errors.append(
            {"row": 1, "error": "Nenhuma linha de dados encontrada (apenas o cabeçalho)."}
        )
    return items, errors

async def apply_stock_import(
    db: AsyncSession, *, integration, filename: str, content: bytes
) -> dict:
    """Importa o estoque de um arquivo (CSV/Excel) e registra a execução.

    Reusa integralmente `apply_stock_sync`: os erros de PARSING do arquivo
    entram como `extra_errors` e são somados aos erros de aplicação.
    """
    rows = list(iter_stock_rows(filename, content))
    items, errors = parse_stock_rows(rows)
    return await apply_stock_sync(
        db,
        integration=integration,
        items=items,
        batch_id=None,
        extra_errors=errors,
    )

# ==================== BLOCO B3 — Registros do webhook ====================
def parse_stock_records(
    records: list[dict], max_records: int = MAX_IMPORT_ROWS
) -> tuple[list[StockItem], list[dict]]:
    """Converte os registros JSON do evento `stock.sync` em StockItem.

    Espelha `parse_stock_rows`, mas para o payload do webhook: o campo
    `index` identifica a posição do registro no array (1-based). Um registro
    inválido NUNCA derruba o lote — vira item em `errors`, e o restante
    continua sendo aplicado.
    """
    items: list[StockItem] = []
    errors: list[dict] = []

    for index, rec in enumerate(records, start=1):
        if index > max_records:
            errors.append(
                {"index": index, "error": f"Evento excede o limite de {max_records} registros."}
            )
            break
        if not isinstance(rec, dict):
            errors.append({"index": index, "error": "Registro inválido (não é objeto)."})
            continue

        sku = str(rec.get("sku") or "").strip()
        if not sku:
            errors.append({"index": index, "error": "Registro sem SKU."})
            continue

        raw_stock = rec.get("stock")
        if raw_stock is None or str(raw_stock).strip() == "":
            errors.append({"index": index, "sku": sku, "error": "Registro sem valor de estoque."})
            continue

        external_id = rec.get("external_id")
        external_id = str(external_id).strip() if external_id is not None else ""
        try:
            item = StockItem.model_validate(
                {
                    "sku": sku,
                    "stock": raw_stock,
                    "external_id": external_id or None,
                }
            )
        except ValidationError as exc:
            errors.append({"index": index, "sku": sku, "error": _first_error(exc)})
            continue

        items.append(item)

    return items, errors

# ==================== Motor comum de aplicação ====================
async def apply_stock_sync(
    db: AsyncSession,
    *,
    integration,
    items,
    batch_id: str | None = None,
    extra_errors: list[dict] | None = None,
) -> dict:
    """Aplica um lote de estoque e registra a execução.

    `extra_errors` (Blocos B2/B3): erros de PARSING (linha/registro inválido,
    SKU ausente, estoque inválido). Já vêm com o identificador da origem e são
    somados aos erros de aplicação, sem interromper o resto do lote.

    Retorna um dict pronto para `StockSyncResult`:
    {sync_id, status, processed, unchanged, errors, message, details}
    """
    tenant_id = integration.tenant_id
    now = datetime.now(timezone.utc)

    # 1) Idempotência: o mesmo lote nunca é aplicado 2x (retry do agente).
    if batch_id and not await _claim_batch(tenant_id, batch_id):
        sync = SyncExecution(
            tenant_id=tenant_id,
            integration_id=integration.id,
            entity=ENTITY,
            status=SyncStatus.SUCCESS,
            processed=0,
            errors=0,
            started_at=now,
            finished_at=now,
            message="Lote já processado anteriormente (idempotência).",
        )
        db.add(sync)
        await db.flush()
        return {
            "sync_id": sync.id,
            "status": "duplicate",
            "processed": 0,
            "unchanged": 0,
            "errors": 0,
            "message": sync.message,
            "details": [],
        }

    # 2) Registra a execução como RUNNING (trilha de auditoria).
    sync = SyncExecution(
        tenant_id=tenant_id,
        integration_id=integration.id,
        entity=ENTITY,
        status=SyncStatus.RUNNING,
        started_at=now,
    )
    db.add(sync)
    await db.flush()

    # 3) Deduplica o lote por SKU (última ocorrência vence) e resolve em 1 query.
    wanted: dict[str, StockItem] = {}
    for item in items:
        key = (item.sku or "").strip().lower()
        if key:
            wanted[key] = item

    found: dict[str, Product] = {}
    if wanted:
        result = await db.execute(
            select(Product).where(
                Product.tenant_id == tenant_id,
                Product.is_deleted == False,  # noqa: E712 — ignora soft delete
                func.lower(Product.sku).in_(list(wanted.keys())),
            )
        )
        found = {(p.sku or "").lower(): p for p in result.scalars().all()}

    # 4) Aplica item a item (só grava o que realmente mudou).
    #    Os erros de parsing (B2/B3) entram na contagem desde o início.
    processed = 0
    unchanged = 0
    errors = len(extra_errors or [])
    details: list[dict] = list(extra_errors or [])

    for key, item in wanted.items():
        product = found.get(key)
        if product is None:
            errors += 1
            if len(details) < MAX_DETAILS:
                details.append(
                    {"sku": item.sku, "error": "SKU não encontrado no catálogo."}
                )
            continue
        if product.stock == item.stock:
            unchanged += 1
            continue
        product.stock = item.stock
        processed += 1

    # 5) Fecha a execução com o resumo (seção 33).
    if errors == 0:
        final_status = SyncStatus.SUCCESS
        label = "ok"
    elif processed or unchanged:
        final_status = SyncStatus.SUCCESS
        label = "partial"
    else:
        final_status = SyncStatus.FAILED
        label = "failed"

    sync.status = final_status
    sync.processed = processed
    sync.errors = errors
    sync.finished_at = datetime.now(timezone.utc)
    sync.message = _message(processed, unchanged, errors)
    await db.flush()

    return {
        "sync_id": sync.id,
        "status": label,
        "processed": processed,
        "unchanged": unchanged,
        "errors": errors,
        "message": sync.message,
        "details": details,
    }