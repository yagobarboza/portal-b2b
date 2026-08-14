"""Serviço de precificação (seção 17 do doc).

Regra de prioridade do preço final:
1. Preço específico do cliente (CustomerPrice)
2. Preço da tabela (PriceList)
3. Preço padrão do produto

O preço do frontend NUNCA é confiável — o backend sempre recalcula
o preço final a partir da base de dados (seção 17).
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CustomerPrice, Product
from app.repositories.catalog import CustomerPriceRepository

async def calculate_product_price(
    db: AsyncSession,
    product: Product,
    customer_id: UUID | None = None,
) -> tuple[Decimal, str]:
    """Calcula o preço final de um produto para um cliente.

    Retorna (preço_final, fonte_do_preço).
    """
    # 1. Preço específico do cliente (maior prioridade)
    if customer_id:
        repo = CustomerPriceRepository(db)
        cp = await repo.get_for_product(customer_id, product.id)
        if cp:
            return cp.price, "customer"

    # 2. Preço padrão do produto (seção 17)
    # (Tabela de preço pode ser adicionada aqui quando implementada por completo)
    return product.price, "default"