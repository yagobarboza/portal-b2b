"""Adapters configuráveis para payloads baseados em campos/JSON paths."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.integrations.contracts import NormalizedProduct, StockUpdate
from app.integrations.interfaces import RawRecord


def read_path(record: Mapping[str, Any], path: str | None) -> Any:
    """Lê `a.b.0.c` em dicionários/listas; caminho vazio retorna None."""
    if not path:
        return None
    value: Any = record
    for part in path.split("."):
        if isinstance(value, Mapping):
            value = value.get(part)
        elif isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return value


@dataclass(frozen=True, slots=True)
class MappingStockAdapter:
    sku_field: str = "sku"
    stock_field: str = "stock"
    external_id_field: str | None = "external_id"
    occurred_at_field: str | None = "occurred_at"
    source_version_field: str | None = "source_version"

    def adapt_stock(self, record: RawRecord) -> StockUpdate:
        return StockUpdate.model_validate(
            {
                "sku": read_path(record, self.sku_field),
                "stock": read_path(record, self.stock_field),
                "external_id": read_path(record, self.external_id_field),
                "occurred_at": read_path(record, self.occurred_at_field),
                "source_version": read_path(record, self.source_version_field),
            }
        )


@dataclass(frozen=True, slots=True)
class MappingProductAdapter:
    """Adapter de produto configurável sem código específico do ERP."""

    fields: Mapping[str, str] = field(
        default_factory=lambda: {
            "sku": "sku",
            "external_id": "external_id",
            "code": "code",
            "name": "name",
            "description": "description",
            "brand": "brand",
            "category_external_id": "category_external_id",
            "category_name": "category_name",
            "unit": "unit",
            "price": "price",
            "stock": "stock",
            "image_url": "image_url",
            "status": "status",
            "ean": "ean",
            "attributes": "attributes",
            "variations": "variations",
            "weight": "weight",
            "width": "width",
            "height": "height",
            "length": "length",
            "occurred_at": "occurred_at",
        }
    )

    def adapt_product(self, record: RawRecord) -> NormalizedProduct:
        values = {}
        for target, source in self.fields.items():
            value = read_path(record, source)
            if value is not None:
                values[target] = value
        # Nome é obrigatório no contrato; o SKU é um fallback explícito para
        # ERPs cujo catálogo só expõe código e descrição em outra etapa.
        values["name"] = values.get("name") or values.get("sku")
        return NormalizedProduct.model_validate(values)
