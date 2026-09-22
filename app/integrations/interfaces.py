"""Interfaces por capability para connectors e adapters de ERP."""

from collections.abc import AsyncIterator, Mapping
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.integrations.contracts import NormalizedProduct, StockUpdate

RawRecord = Mapping[str, Any]


class Capability(StrEnum):
    PRODUCTS = "products"
    STOCK = "stock"


@runtime_checkable
class StockConnector(Protocol):
    """Transporta registros de estoque sem conhecer o domínio interno."""

    async def fetch_stock(self) -> AsyncIterator[RawRecord]: ...


@runtime_checkable
class ProductConnector(Protocol):
    """Transporta registros completos de produto."""

    async def fetch_products(self) -> AsyncIterator[RawRecord]: ...


@runtime_checkable
class StockAdapter(Protocol):
    """Converte um registro externo em atualização canônica de estoque."""

    def adapt_stock(self, record: RawRecord) -> StockUpdate: ...


@runtime_checkable
class ProductAdapter(Protocol):
    """Converte um registro externo em produto canônico."""

    def adapt_product(self, record: RawRecord) -> NormalizedProduct: ...
