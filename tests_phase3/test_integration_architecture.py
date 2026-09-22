"""Regressões dos contratos e extensões introduzidos na FASE 3."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.integrations.adapters import MappingProductAdapter, MappingStockAdapter
from app.integrations.connectors.rest_json import stock_adapter_factory
from app.integrations.contracts import NormalizedProduct, StockUpdate, normalize_sku
from app.integrations.interfaces import Capability, StockAdapter, StockConnector
from app.integrations.registry import IntegrationRegistry
from app.schemas.integration import StockSyncRequest
from app.services.integration import normalize_product_records
from app.services.stock_sync import parse_stock_records, parse_stock_rows


def test_sku_and_stock_are_normalized_once_at_the_boundary() -> None:
    update = StockUpdate(sku="  ab  123 ", stock="15,9", external_id="ext-1")

    assert update.sku == "AB 123"
    assert update.stock == 15
    assert normalize_sku("ab 123") == update.sku


def test_stock_contract_rejects_invalid_balance() -> None:
    with pytest.raises(ValidationError):
        StockUpdate(sku="ABC", stock=-1)


def test_mapping_stock_adapter_supports_nested_erp_fields() -> None:
    adapter = MappingStockAdapter(
        sku_field="item.codigo",
        stock_field="saldo.disponivel",
        external_id_field="item.id",
    )

    update = adapter.adapt_stock(
        {
            "item": {"id": "42", "codigo": " sku-1 "},
            "saldo": {"disponivel": "7"},
        }
    )

    assert update == StockUpdate(sku="SKU-1", stock=7, external_id="42")
    assert isinstance(adapter, StockAdapter)


def test_all_four_current_channels_emit_stock_update() -> None:
    agent = StockSyncRequest(
        batch_id="batch-1", items=[{"sku": " agent-1 ", "stock": "1"}]
    ).items
    file_items, file_errors = parse_stock_rows(
        [{"sku": " file-1 ", "estoque": "2"}]
    )
    webhook_items, webhook_errors = parse_stock_records(
        [{"sku": " webhook-1 ", "stock": "3"}]
    )
    rest_adapter = stock_adapter_factory(
        config={"sku_field": "codigo", "stock_field": "saldo"}
    )
    rest_items, rest_errors = parse_stock_records(
        [{"codigo": " rest-1 ", "saldo": "4"}], adapter=rest_adapter
    )

    assert [item.sku for item in agent] == ["AGENT-1"]
    assert [item.sku for item in file_items] == ["FILE-1"]
    assert [item.sku for item in webhook_items] == ["WEBHOOK-1"]
    assert [item.sku for item in rest_items] == ["REST-1"]
    assert not file_errors and not webhook_errors and not rest_errors
    assert all(
        isinstance(item, StockUpdate)
        for item in [*agent, *file_items, *webhook_items, *rest_items]
    )


def test_product_adapter_outputs_canonical_product() -> None:
    adapter = MappingProductAdapter(
        fields={
            "external_id": "id",
            "sku": "codigo",
            "name": "descricao",
            "price": "preco.valor",
            "stock": "saldo",
        }
    )

    product = adapter.adapt_product(
        {
            "id": "erp-9",
            "codigo": " prod-9 ",
            "descricao": "Produto nove",
            "preco": {"valor": "19.90"},
            "saldo": "3",
        }
    )

    assert product == NormalizedProduct(
        external_id="erp-9",
        sku="PROD-9",
        name="Produto nove",
        price=Decimal("19.90"),
        stock=3,
    )


def test_invalid_product_isolated_from_valid_records() -> None:
    products, errors = normalize_product_records(
        [
            {"external_id": "1", "sku": "a-1", "name": "Produto A"},
            {"external_id": "2", "name": "Sem SKU"},
        ]
    )

    assert [product.sku for product in products] == ["A-1"]
    assert len(errors) == 1
    assert errors[0]["index"] == 2


@pytest.mark.asyncio
async def test_registry_resolves_implementation_by_capability() -> None:
    class Connector:
        async def fetch_stock(self):
            yield {"sku": "A", "stock": 1}

    registry = IntegrationRegistry()
    registry.register_connector("erp-x", Capability.STOCK, Connector)
    connector = registry.connector("ERP-X", Capability.STOCK)

    assert isinstance(connector, StockConnector)
    assert [record async for record in connector.fetch_stock()] == [
        {"sku": "A", "stock": 1}
    ]
