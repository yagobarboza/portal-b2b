"""Contratos internos independentes do transporte e do ERP.

Todo connector/adapter deve produzir estes modelos antes de chamar as regras
de catálogo ou estoque. Assim, payloads externos nunca chegam diretamente aos
modelos SQLAlchemy.
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_STOCK_VALUE = 1_000_000
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def normalize_sku(value: Any) -> str:
    """Cria a chave canônica de SKU usada pelo domínio e pelo banco.

    Preserva pontuação, mas remove espaços periféricos, compacta espaços
    internos e torna a comparação independente de caixa.
    """
    if value is None:
        raise ValueError("SKU ausente.")
    normalized = _WHITESPACE.sub(" ", str(value).strip()).upper()
    if not normalized:
        raise ValueError("SKU vazio.")
    if _CONTROL_CHARS.search(normalized):
        raise ValueError("SKU contém caracteres de controle.")
    if len(normalized) > 80:
        raise ValueError("SKU excede 80 caracteres.")
    return normalized


def normalize_stock(value: Any) -> int:
    """Normaliza saldos externos para a unidade inteira usada pelo portal."""
    if isinstance(value, bool):
        raise ValueError("Estoque inválido.")  # noqa: TRY004 - erro de domínio
    if isinstance(value, str):
        clean = value.strip().replace(" ", "").replace(",", ".")
        if not clean:
            raise ValueError("Estoque vazio.")
    else:
        clean = value
    try:
        stock = int(Decimal(str(clean)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Estoque inválido: {value!r}") from exc
    if stock < 0:
        raise ValueError("Estoque não pode ser negativo.")
    if stock > MAX_STOCK_VALUE:
        raise ValueError(f"Estoque acima do limite ({MAX_STOCK_VALUE}).")
    return stock


class StockUpdate(BaseModel):
    """Atualização canônica e enxuta de saldo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(min_length=1, max_length=80)
    stock: int = Field(ge=0, le=MAX_STOCK_VALUE)
    external_id: str | None = Field(None, max_length=120)
    occurred_at: datetime | None = None
    source_version: str | None = Field(None, max_length=120)

    @field_validator("sku", mode="before")
    @classmethod
    def _normalize_sku(cls, value: Any) -> str:
        return normalize_sku(value)

    @field_validator("stock", mode="before")
    @classmethod
    def _normalize_stock(cls, value: Any) -> int:
        return normalize_stock(value)


class NormalizedProduct(BaseModel):
    """Produto canônico produzido por qualquer adapter de ERP.

    Campos que ainda não possuem coluna no catálogo ficam disponíveis para
    connectors futuros e não são persistidos silenciosamente pelo serviço.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(min_length=1, max_length=80)
    external_id: str | None = Field(None, max_length=200)
    code: str | None = Field(None, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    brand: str | None = Field(None, max_length=100)
    category_external_id: str | None = Field(None, max_length=200)
    category_name: str | None = Field(None, max_length=150)
    unit: str | None = Field(None, max_length=20)
    price: Decimal | None = Field(None, ge=0)
    stock: int | None = Field(None, ge=0, le=MAX_STOCK_VALUE)
    image_url: str | None = Field(None, max_length=2048)
    status: Literal["active", "inactive"] | None = None
    ean: str | None = Field(None, max_length=32)
    attributes: dict[str, Any] = Field(default_factory=dict)
    variations: list[dict[str, Any]] = Field(default_factory=list)
    weight: Decimal | None = Field(None, ge=0)
    width: Decimal | None = Field(None, ge=0)
    height: Decimal | None = Field(None, ge=0)
    length: Decimal | None = Field(None, ge=0)
    occurred_at: datetime | None = None

    @field_validator("sku", mode="before")
    @classmethod
    def _normalize_sku(cls, value: Any) -> str:
        return normalize_sku(value)

    @field_validator("stock", mode="before")
    @classmethod
    def _normalize_optional_stock(cls, value: Any) -> int | None:
        return None if value is None else normalize_stock(value)

    @field_validator(
        "external_id",
        "code",
        "description",
        "brand",
        "category_external_id",
        "category_name",
        "unit",
        "image_url",
        "ean",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value
