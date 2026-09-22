"""Dry-run seguro de connector/adapter sem qualquer escrita de domínio."""

from pydantic import ValidationError

from app.core.redaction import redact_item_errors
from app.integrations.connectors import rest_json as _rest_json  # noqa: F401
from app.integrations.interfaces import Capability
from app.integrations.registry import integration_registry

MAX_PREVIEW_RECORDS = 500


async def preview_connector(
    config: dict, *, entity: str, sample_size: int = 10
) -> dict:
    capability = Capability.PRODUCTS if entity == "products" else Capability.STOCK
    connector_name = str(config.get("_connector") or "rest_json")
    connector = integration_registry.connector(
        connector_name, capability, config=config
    )
    adapter = integration_registry.adapter(
        connector_name, capability, config=config
    )
    iterator = (
        connector.fetch_products()
        if capability == Capability.PRODUCTS
        else connector.fetch_stock()
    )
    received = 0
    valid = 0
    errors: list[dict] = []
    sample: list[dict] = []
    async for record in iterator:
        received += 1
        if received > MAX_PREVIEW_RECORDS:
            errors.append(
                {"index": received, "code": "preview_limit", "error": "Amostra excede 500 registros."}
            )
            break
        try:
            normalized = (
                adapter.adapt_product(record)
                if capability == Capability.PRODUCTS
                else adapter.adapt_stock(record)
            )
            valid += 1
            if len(sample) < sample_size:
                values = normalized.model_dump(mode="json")
                allowed = (
                    {"sku", "external_id", "name", "price", "stock", "status"}
                    if capability == Capability.PRODUCTS
                    else {"sku", "external_id", "stock", "occurred_at", "source_version"}
                )
                sample.append({key: values.get(key) for key in allowed if values.get(key) is not None})
        except ValidationError as exc:
            error = str(exc.errors()[0].get("msg", "Registro inválido."))[:200]
            errors.append(
                {"index": received, "code": "mapping_error", "error": error}
            )
        except (ValueError, TypeError) as exc:
            errors.append(
                {
                    "index": received,
                    "code": "mapping_error",
                    "error": f"Registro incompatível ({exc.__class__.__name__}).",
                }
            )
    safe_errors, truncated = redact_item_errors(errors, limit=20)
    total_errors = len(errors)
    if truncated:
        safe_errors.append({"code": "truncated", "error": f"{truncated} erro(s) adicional(is) omitido(s)."})
    return {
        "ok": total_errors == 0 and valid > 0,
        "entity": entity,
        "received": received,
        "valid": valid,
        "errors": total_errors,
        "message": f"Dry-run concluído: {valid} válido(s), {total_errors} erro(s).",
        "sample": sample,
        "details": safe_errors,
    }
