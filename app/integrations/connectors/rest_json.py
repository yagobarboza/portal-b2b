"""Connector REST/JSON reutilizável para produtos e estoque."""

import json
from typing import Any

import httpx

from app.core.crypto import decrypt_str
from app.core.network_security import validate_public_https_url
from app.integrations.adapters import MappingProductAdapter, MappingStockAdapter
from app.integrations.interfaces import Capability
from app.integrations.registry import integration_registry

HTTP_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def decrypt_connector_config(config: dict) -> dict:
    """Decifra credenciais somente em memória para a chamada externa."""
    output = dict(config)
    for field in ("token", "username", "password"):
        if output.get(field):
            output[field] = decrypt_str(output[field])
    output["headers"] = {
        key: decrypt_str(value)
        for key, value in (output.get("headers") or {}).items()
    }
    return output


async def fetch_json(config: dict) -> tuple[int, Any]:
    """Executa GET seguro, limitado e sem redirects/proxy do ambiente."""
    decrypted = decrypt_connector_config(config)
    url = f"{decrypted['base_url']}/{decrypted['path'].lstrip('/')}"
    await validate_public_https_url(url)
    headers = dict(decrypted.get("headers") or {})
    auth = None
    params = None
    cursor_param = str(decrypted.get("cursor_param") or "").strip()
    cursor = decrypted.get("_cursor")
    if cursor_param and cursor is not None:
        params = {cursor_param: str(cursor)}
    if decrypted.get("auth_type") == "bearer" and decrypted.get("token"):
        headers["Authorization"] = f"Bearer {decrypted['token']}"
    elif decrypted.get("auth_type") == "basic":
        auth = (decrypted.get("username", ""), decrypted.get("password", ""))

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
    ) as client, client.stream(
        "GET", url, headers=headers, auth=auth, params=params
    ) as response:
        if response.is_redirect:
            raise httpx.HTTPStatusError(
                "Redirects não são permitidos para integrações.",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        content_length = int(response.headers.get("content-length", "0") or 0)
        if content_length > MAX_RESPONSE_BYTES:
            raise ValueError("Resposta da API excede o limite permitido.")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ValueError("Resposta da API excede o limite permitido.")
            chunks.append(chunk)
        return response.status_code, json.loads(b"".join(chunks))


def navigate_json(data: Any, path: str) -> Any:
    """Navega um caminho `a.b.0.c` em uma resposta JSON."""
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


class RestJsonStockConnector:
    def __init__(self, *, config: dict) -> None:
        self.config = config
        self.next_cursor = None

    async def fetch_stock(self):
        _, payload = await fetch_json(self.config)
        self.next_cursor = navigate_json(
            payload, self.config.get("next_cursor_path", "")
        )
        items = navigate_json(payload, self.config.get("data_path", ""))
        if not isinstance(items, list):
            raise ValueError(  # noqa: TRY004 - payload externo inválido
                "data_path não aponta para um array de itens."
            )
        for item in items:
            if isinstance(item, dict):
                yield item


class RestJsonProductConnector:
    def __init__(self, *, config: dict) -> None:
        self.config = config

    async def fetch_products(self):
        _, payload = await fetch_json(self.config)
        items = navigate_json(payload, self.config.get("data_path", ""))
        if not isinstance(items, list):
            raise ValueError(  # noqa: TRY004 - payload externo inválido
                "data_path não aponta para um array de itens."
            )
        for item in items:
            if isinstance(item, dict):
                yield item


def stock_adapter_factory(*, config: dict) -> MappingStockAdapter:
    return MappingStockAdapter(
        sku_field=config.get("sku_field", "sku"),
        stock_field=config.get("stock_field", "stock"),
        external_id_field=config.get("external_id_field", "external_id"),
        occurred_at_field=config.get("occurred_at_field") or None,
        source_version_field=config.get("source_version_field") or None,
    )


def product_adapter_factory(*, config: dict) -> MappingProductAdapter:
    fields = config.get("product_fields")
    return MappingProductAdapter(**({"fields": fields} if fields else {}))


integration_registry.register_connector(
    "rest_json", Capability.STOCK, RestJsonStockConnector
)
integration_registry.register_adapter(
    "rest_json", Capability.STOCK, stock_adapter_factory
)
integration_registry.register_connector(
    "rest_json", Capability.PRODUCTS, RestJsonProductConnector
)
integration_registry.register_adapter(
    "rest_json", Capability.PRODUCTS, product_adapter_factory
)
