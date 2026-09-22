"""Orquestra connectors/adapters por capability sem conhecer o ERP."""

from pydantic import ValidationError

from app.integrations.connectors import rest_json as _rest_json  # noqa: F401
from app.integrations.interfaces import Capability
from app.integrations.registry import integration_registry
from app.services.product_sync import ProductSyncService

MAX_PRODUCT_RECORDS = 20_000


async def fetch_and_apply_products(
    db,
    *,
    integration,
    config: dict,
    sync_execution=None,
) -> dict:
    """Executa qualquer connector+adapter registrado para produtos."""
    connector_name = str(config.get("_connector") or "rest_json")
    connector = integration_registry.connector(
        connector_name, Capability.PRODUCTS, config=config
    )
    adapter = integration_registry.adapter(
        connector_name, Capability.PRODUCTS, config=config
    )

    products = []
    errors: list[dict] = []
    index = 0
    async for record in connector.fetch_products():
        index += 1
        if index > MAX_PRODUCT_RECORDS:
            errors.append(
                {
                    "index": index,
                    "error": f"Resposta excede {MAX_PRODUCT_RECORDS} produtos.",
                }
            )
            break
        try:
            products.append(adapter.adapt_product(record))
        except ValidationError as exc:
            errors.append(
                {
                    "index": index,
                    "error": str(exc.errors()[0].get("msg", "Produto inválido."))[
                        :200
                    ],
                }
            )

    return await ProductSyncService(db).sync(
        integration=integration,
        products=products,
        sync_execution=sync_execution,
        input_errors=errors,
    )
