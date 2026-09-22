"""Conector de PULL da API REST do ERP do cliente (Bloco B4 — tipo `api`).

Fluxo:
    API do ERP do cliente → httpx (GET) → mapeia campos → apply_stock_sync

Reusa o MESMO motor de estoque (`apply_stock_sync`) e o parser de registros
(`parse_stock_records`): normalização de inteiro, só atualiza produto
existente, registra SyncExecution e aceita itens inválidos sem derrubar o lote.

Segurança:
- As credenciais do cliente ficam CIFRADAS (Fernet) em coluna dedicada.
- O tenant vem da integração (nunca da resposta da API do cliente).
- Timeout e limite de itens (anti-DoS).
"""
import logging
from typing import Any

import httpx

from app.core.crypto import encrypt_str
from app.core.network_security import UnsafeOutboundUrlError
from app.integrations.connectors.rest_json import fetch_json, navigate_json
from app.integrations.interfaces import Capability
from app.integrations.registry import integration_registry
from app.schemas.integration import MAX_IMPORT_ROWS, ApiPullConfigIn
from app.services.stock_sync import apply_stock_sync, parse_stock_records

logger = logging.getLogger("api_pull")

_BLOCKED_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "host",
    "proxy-authorization",
    "transfer-encoding",
}

# ---------- Helpers de config ----------
def _secret_value(
    *,
    mode: str,
    value: str | None,
    existing: dict,
    key: str,
) -> str | None:
    if mode == "keep":
        return existing.get(key)
    if mode == "clear":
        return None
    clean = (value or "").strip()
    if not clean:
        raise ValueError(f"{key} deve ser informado para substituir o segredo.")
    return encrypt_str(clean)


def build_stored_config(
    body: ApiPullConfigIn, existing: dict | None = None
) -> dict:
    """Monta a config persistível: segredos CIFRADOS, demais campos em claro.

    ✅ CORREÇÃO: os segredos são CIFRADOS com `encrypt_str` (antes usava-se
    `decrypt_str` por engano, o que gravava em texto puro).
    """
    previous = existing or {}
    cfg: dict[str, Any] = {
        "base_url": body.base_url.rstrip("/"),
        "path": body.path or "/",
        "auth_type": body.auth_type,
        "data_path": body.data_path or "",
        "sku_field": body.sku_field or "sku",
        "stock_field": body.stock_field or "stock",
        "external_id_field": body.external_id_field or "external_id",
        "occurred_at_field": body.occurred_at_field or "",
        "source_version_field": body.source_version_field or "",
        "cursor_param": body.cursor_param or "",
        "next_cursor_path": body.next_cursor_path or "",
        "product_fields": dict(body.product_fields or {}),
        "interval_minutes": body.interval_minutes,
    }
    if previous.get("_connector"):
        cfg["_connector"] = previous["_connector"]

    if body.headers_mode == "keep":
        cfg["headers"] = dict(previous.get("headers") or {})
    elif body.headers_mode == "clear":
        cfg["headers"] = {}
    else:
        clean_headers: dict[str, str] = {}
        for raw_key, raw_value in (body.headers or {}).items():
            key = raw_key.strip()
            value = raw_value.strip()
            if not key or not value:
                raise ValueError("Cabeçalhos substituídos não podem ter valores vazios.")
            if key.lower() in _BLOCKED_HEADERS:
                raise ValueError(f"Cabeçalho não permitido: {key}.")
            clean_headers[key] = encrypt_str(value)
        cfg["headers"] = clean_headers

    # Apenas os segredos do modo de autenticação selecionado são preservados.
    if body.auth_type == "bearer":
        token = _secret_value(
            mode=body.token_mode,
            value=body.token,
            existing=previous,
            key="token",
        )
        if not token:
            raise ValueError("Autenticação bearer exige token configurado.")
        cfg["token"] = token
    if body.auth_type == "basic":
        username = _secret_value(
            mode=body.username_mode,
            value=body.username,
            existing=previous,
            key="username",
        )
        password = _secret_value(
            mode=body.password_mode,
            value=body.password,
            existing=previous,
            key="password",
        )
        if not username or not password:
            raise ValueError("Autenticação basic exige usuário e senha configurados.")
        cfg["username"] = username
        cfg["password"] = password
    return cfg

def masked_config(cfg: dict) -> dict:
    """Visão de leitura: segredos viram apenas flags de presença."""
    return {
        "base_url": cfg.get("base_url", ""),
        "path": cfg.get("path", "/"),
        "auth_type": cfg.get("auth_type", "none"),
        "data_path": cfg.get("data_path", ""),
        "sku_field": cfg.get("sku_field", "sku"),
        "stock_field": cfg.get("stock_field", "stock"),
        "external_id_field": cfg.get("external_id_field", "external_id"),
        "occurred_at_field": cfg.get("occurred_at_field", ""),
        "source_version_field": cfg.get("source_version_field", ""),
        "cursor_param": cfg.get("cursor_param", ""),
        "next_cursor_path": cfg.get("next_cursor_path", ""),
        "product_fields": dict(cfg.get("product_fields") or {}),
        "interval_minutes": cfg.get("interval_minutes", 15),
        "token_set": bool(cfg.get("token")),
        "username_set": bool(cfg.get("username")),
        "password_set": bool(cfg.get("password")),
        "header_keys": list((cfg.get("headers") or {}).keys()),
    }

async def test_connection(config: dict) -> dict:
    """Testa a conexão SEM aplicar nada no banco.

    Retorna {ok, status_code, items_found, message}.
    """
    try:
        status_code, payload = await fetch_json(config)
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "status_code": exc.response.status_code,
            "items_found": 0,
            "message": f"API respondeu {exc.response.status_code}.",
        }
    except httpx.RequestError as exc:
        return {
            "ok": False,
            "status_code": None,
            "items_found": 0,
            "message": f"Falha de conexão: {exc.__class__.__name__}.",
        }
    except UnsafeOutboundUrlError as exc:
        return {
            "ok": False,
            "status_code": None,
            "items_found": 0,
            "message": str(exc),
        }
    except ValueError:
        return {
            "ok": False,
            "status_code": None,
            "items_found": 0,
            "message": "Resposta não é JSON válido.",
        }

    items = navigate_json(payload, config.get("data_path", ""))
    if not isinstance(items, list):
        return {
            "ok": False,
            "status_code": status_code,
            "items_found": 0,
            "message": "data_path não aponta para um array de itens.",
        }
    return {
        "ok": True,
        "status_code": status_code,
        "items_found": len(items),
        "message": f"Conexão OK — {len(items)} item(ns) encontrado(s).",
    }

async def fetch_and_apply_stock(
    db, *, integration, config: dict, sync_execution=None
) -> dict:
    """Busca o estoque na API do cliente e aplica via o motor comum.

    Retorna um dict pronto para `StockSyncResult`.
    """
    connector_name = str(config.get("_connector") or "rest_json")
    connector = integration_registry.connector(
        connector_name, Capability.STOCK, config=config
    )
    records = [record async for record in connector.fetch_stock()]

    adapter = integration_registry.adapter(
        connector_name, Capability.STOCK, config=config
    )
    parsed, row_errors = parse_stock_records(
        records,
        max_records=min(
            MAX_IMPORT_ROWS,
            max(1, int(config.get("_max_records") or MAX_IMPORT_ROWS)),
        ),
        adapter=adapter,
    )
    result = await apply_stock_sync(
        db,
        integration=integration,
        items=parsed,
        batch_id=None,
        extra_errors=row_errors,
        sync_execution=sync_execution,
    )
    if config.get("next_cursor_path") and hasattr(connector, "next_cursor"):
        result["_next_cursor"] = getattr(connector, "next_cursor")
    return result
