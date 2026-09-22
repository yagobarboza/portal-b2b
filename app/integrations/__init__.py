"""Contratos e extensões canônicas do módulo de integrações."""

from app.integrations.contracts import NormalizedProduct, StockUpdate, normalize_sku
from app.integrations.interfaces import (
    Capability,
    ProductAdapter,
    ProductConnector,
    StockAdapter,
    StockConnector,
)

__all__ = [
    "Capability",
    "NormalizedProduct",
    "ProductAdapter",
    "ProductConnector",
    "StockAdapter",
    "StockConnector",
    "StockUpdate",
    "normalize_sku",
]
