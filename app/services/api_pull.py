"""Conector de PULL da API REST do ERP do cliente (Bloco B4 — tipo `api`).

Fluxo:
    API do ERP do cliente → httpx (GET) → mapeia campos → apply_stock_sync

Reusa o MESMO motor de estoque (`apply_stock_sync`) e o parser de registros
(`parse_stock_records`): normalização de inteiro, só atualiza produto
existente, registra SyncExecution e aceita itens inválidos sem derrubar o lote.

Segurança:
- As credenciais do cliente ficam CIFRADAS (Fernet) em config_encrypted.
- O tenant vem da integração (nunca da resposta da API do cliente).
- Timeout e limite de itens (anti-DoS).
"""
import logging
from typing import Any

import httpx

from app.core.crypto import decrypt_str, encrypt_str
from app.schemas.integration import ApiPullConfigIn, MAX_IMPORT_ROWS
from app.services.stock_sync import apply_stock_sync, parse_stock_records

logger = logging.getLogger("api_pull")

# Timeout do request ao ERP do cliente (segundos).
HTTP_TIMEOUT = 30.0

# ---------- Helpers de config ----------
def build_stored_config(body: ApiPullConfigIn) -> dict:
    """Monta a config persistível: segredos CIFRADOS, demais campos em claro.

    ✅ CORREÇÃO: os segredos são CIFRADOS com `encrypt_str` (antes usava-se
    `decrypt_str` por engano, o que gravava em texto puro).
    """
    cfg: dict[str, Any] = {
        "base_url": body.base_url.rstrip("/"),
        "path": body.path or "/",
        "auth_type": body.auth_type,
        "data_path": body.data_path or "",
        "sku_field": body.sku_field or "sku",
        "stock_field": body.stock_field or "stock",
        "external_id_field": body.external_id_field or "external_id",
        "interval_minutes": body.interval_minutes,
        "headers": {k: encrypt_str(v) for k, v in (body.headers or {}).items()},
    }
    # Cifra os segredos ANTES de persistir.
    if body.auth_type == "bearer" and body.token:
        cfg["token"] = encrypt_str(body.token)
    if body.auth_type == "basic":
        if body.username:
            cfg["username"] = encrypt_str(body.username)
        if body.password:
            cfg["password"] = encrypt_str(body.password)
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
        "interval_minutes": cfg.get("interval_minutes", 15),
        "token_set": bool(cfg.get("token")),
        "username_set": bool(cfg.get("username")),
        "password_set": bool(cfg.get("password")),
        "header_keys": list((cfg.get("headers") or {}).keys()),
    }

def _decrypt_config(cfg: dict) -> dict:
    """Decifra os segredos para uso no request (nunca persiste em claro)."""
    out = dict(cfg)
    if out.get("token"):
        out["token"] = decrypt_str(out["token"])
    if out.get("username"):
        out["username"] = decrypt_str(out["username"])
    if out.get("password"):
        out["password"] = decrypt_str(out["password"])
    out["headers"] = {
        k: decrypt_str(v) for k, v in (out.get("headers") or {}).items()
    }
    return out

# ---------- HTTP ----------
async def _fetch(config: dict) -> tuple[int, Any]:
    """Executa o GET na API do cliente. Retorna (status_code, payload_json)."""
    cfg = _decrypt_config(config)
    url = f"{cfg['base_url']}/{cfg['path'].lstrip('/')}"
    headers = dict(cfg.get("headers") or {})
    auth = None
    if cfg.get("auth_type") == "bearer" and cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    elif cfg.get("auth_type") == "basic":
        auth = (cfg.get("username", ""), cfg.get("password", ""))

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url, headers=headers, auth=auth)
        resp.raise_for_status()
        return resp.status_code, resp.json()

def _navigate(data: Any, path: str) -> Any:
    """Navega um caminho separado por '.' (dicts e índices de listas)."""
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data

def _map_records(items: list, cfg: dict) -> list[dict]:
    """Converte os itens do ERP em registros {sku, stock, external_id}."""
    sku_field = cfg.get("sku_field", "sku")
    stock_field = cfg.get("stock_field", "stock")
    ext_field = cfg.get("external_id_field", "external_id")
    records: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        records.append(
            {
                "sku": it.get(sku_field),
                "stock": it.get(stock_field),
                "external_id": it.get(ext_field),
            }
        )
    return records

async def test_connection(config: dict) -> dict:
    """Testa a conexão SEM aplicar nada no banco.

    Retorna {ok, status_code, items_found, message}.
    """
    try:
        status_code, payload = await _fetch(config)
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
    except ValueError:
        return {
            "ok": False,
            "status_code": None,
            "items_found": 0,
            "message": "Resposta não é JSON válido.",
        }

    items = _navigate(payload, config.get("data_path", ""))
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

async def fetch_and_apply_stock(db, *, integration, config: dict) -> dict:
    """Busca o estoque na API do cliente e aplica via o motor comum.

    Retorna um dict pronto para `StockSyncResult`.
    """
    try:
        status_code, payload = await _fetch(config)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"API do cliente respondeu {exc.response.status_code}.") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Falha de conexão com a API do cliente: {exc.__class__.__name__}.") from exc
    except ValueError as exc:
        raise RuntimeError("Resposta da API do cliente não é JSON válido.") from exc

    items = _navigate(payload, config.get("data_path", ""))
    if not isinstance(items, list):
        raise RuntimeError("data_path não aponta para um array de itens.")

    records = _map_records(items, config)
    parsed, row_errors = parse_stock_records(records, max_records=MAX_IMPORT_ROWS)
    return await apply_stock_sync(
        db,
        integration=integration,
        items=parsed,
        batch_id=None,
        extra_errors=row_errors,
    )