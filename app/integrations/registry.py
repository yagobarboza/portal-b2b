"""Registro explícito de implementações por ERP e capability."""

from collections.abc import Callable
from typing import Any

from app.integrations.interfaces import Capability

ConnectorFactory = Callable[..., Any]
AdapterFactory = Callable[..., Any]


class IntegrationRegistry:
    def __init__(self) -> None:
        self._connectors: dict[tuple[str, Capability], ConnectorFactory] = {}
        self._adapters: dict[tuple[str, Capability], AdapterFactory] = {}

    @staticmethod
    def _key(provider: str, capability: Capability) -> tuple[str, Capability]:
        clean = provider.strip().lower()
        if not clean:
            raise ValueError("Provider da integração não pode ser vazio.")
        return clean, capability

    def register_connector(
        self, provider: str, capability: Capability, factory: ConnectorFactory
    ) -> None:
        self._connectors[self._key(provider, capability)] = factory

    def register_adapter(
        self, provider: str, capability: Capability, factory: AdapterFactory
    ) -> None:
        self._adapters[self._key(provider, capability)] = factory

    def connector(self, provider: str, capability: Capability, **kwargs):
        try:
            factory = self._connectors[self._key(provider, capability)]
        except KeyError as exc:
            raise LookupError(
                f"Connector não registrado: {provider}/{capability.value}."
            ) from exc
        return factory(**kwargs)

    def adapter(self, provider: str, capability: Capability, **kwargs):
        try:
            factory = self._adapters[self._key(provider, capability)]
        except KeyError as exc:
            raise LookupError(
                f"Adapter não registrado: {provider}/{capability.value}."
            ) from exc
        return factory(**kwargs)


integration_registry = IntegrationRegistry()
