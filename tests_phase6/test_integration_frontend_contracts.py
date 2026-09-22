import pytest

from app.main import app
from app.schemas.integration import ApiPullConfigIn, ERPIntegrationUpdate
from app.services.api_pull import build_stored_config, masked_config
from app.services.integration_preview import preview_connector


def test_openapi_exposes_phase6_integration_contracts() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/integrations/{integration_id}" in paths
    assert "patch" in paths["/api/v1/integrations/{integration_id}"]
    assert "/api/v1/integrations/{integration_id}/runs" in paths
    assert "/api/v1/integrations/{integration_id}/runs/{sync_id}" in paths
    assert "/api/v1/integrations/{integration_id}/sync-all" in paths
    assert "/api/v1/integrations/{integration_id}/api-config/dry-run" in paths


def test_update_requires_name_or_status() -> None:
    with pytest.raises(ValueError):
        ERPIntegrationUpdate()


def test_product_mapping_round_trips_without_exposing_secrets(monkeypatch) -> None:
    monkeypatch.setattr("app.services.api_pull.encrypt_str", lambda value: f"enc:{value}")
    body = ApiPullConfigIn(
        base_url="https://erp.example.com",
        auth_type="bearer",
        token="secret",
        token_mode="replace",
        product_fields={"sku": "codigo", "name": "descricao", "stock": "saldo"},
    )
    stored = build_stored_config(body)
    visible = masked_config(stored)
    assert stored["token"] == "enc:secret"
    assert visible["token_set"] is True
    assert "token" not in visible
    assert visible["product_fields"] == body.product_fields


@pytest.mark.asyncio
async def test_dry_run_normalizes_without_writing(monkeypatch) -> None:
    class Connector:
        async def fetch_stock(self):
            yield {"sku": " abc ", "stock": "4"}
            yield {"sku": "bad", "stock": "not-a-number"}

    class Adapter:
        def adapt_stock(self, record):
            from app.integrations.adapters import MappingStockAdapter

            return MappingStockAdapter().adapt_stock(record)

    monkeypatch.setattr(
        "app.services.integration_preview.integration_registry.connector",
        lambda *args, **kwargs: Connector(),
    )
    monkeypatch.setattr(
        "app.services.integration_preview.integration_registry.adapter",
        lambda *args, **kwargs: Adapter(),
    )
    result = await preview_connector({}, entity="stock", sample_size=5)
    assert result["received"] == 2
    assert result["valid"] == 1
    assert result["errors"] == 1
    assert result["sample"] == [{"sku": "ABC", "stock": 4}]
    assert set(result["details"][0]) <= {"row", "index", "sku", "error", "code"}


def test_secret_modes_keep_replace_clear_are_in_openapi() -> None:
    properties = app.openapi()["components"]["schemas"]["ApiPullConfigIn"]["properties"]
    for field in ("token_mode", "username_mode", "password_mode", "headers_mode"):
        assert set(properties[field]["enum"]) == {"keep", "replace", "clear"}
