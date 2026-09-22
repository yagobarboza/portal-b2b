"""Connectors nativos fornecidos pelo PortalB2B."""

from app.integrations.connectors.rest_json import (
    RestJsonProductConnector,
    RestJsonStockConnector,
)

__all__ = ["RestJsonProductConnector", "RestJsonStockConnector"]
