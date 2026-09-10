"""Serviço de precificação (seção 17 do doc).

Regra de prioridade do preço final:
1. Preço específico do cliente (CustomerPrice)
2. Preço da tabela (PriceList)
3. Preço padrão do produto

Depois do preço base, aplica-se o DESCONTO POR QUANTIDADE (Desconto
Progressivo) quando `quantity` é informado:
- VOLUME model: a faixa atingida vale para TODAS as unidades.
- Regra específica do cliente vence a regra global (most specific wins).

O preço do frontend NUNCA é confiável — o backend sempre recalcula
o preço final a partir da base de dados (seção 17).
"""
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product
from app.models.enums import DiscountType
from app.repositories.catalog import CustomerPriceRepository
from app.repositories.discount import QuantityDiscountRepository

async def calculate_product_price(
    db: AsyncSession,
    product: Product,
    customer_id: UUID | None = None,
    quantity: int | None = None,
) -> tuple[Decimal, str]:
    """Calcula o preço final de um produto para um cliente.

    Retorna (preço_final, fonte_do_preço).
    - quantity: quando informado, aplica o desconto por quantidade
      (VOLUME model — a faixa atingida vale para TODAS as unidades).
    """
    # 1. Preço base (prioridade: cliente > padrão)
    if customer_id:
        cp = await CustomerPriceRepository(db).get_for_product(
            customer_id, product.id
        )
        if cp:
            price, source = cp.price, "customer"
        else:
            price, source = product.price, "default"
    else:
        price, source = product.price, "default"

    # 2. Desconto por quantidade (Desconto Progressivo)
    if quantity is not None and quantity > 0:
        discount = await _resolve_quantity_discount(
            db, product.id, customer_id, quantity
        )
        if discount is not None:
            if discount.discount_type == DiscountType.PERCENT:
                price = price * (
                    1 - discount.discount_value / Decimal("100")
                )
            else:  # FIXED — desconto em R$ por unidade
                price = price - discount.discount_value
            price = max(price, Decimal("0")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

    return price, source

async def _resolve_quantity_discount(
    db: AsyncSession,
    product_id: UUID,
    customer_id: UUID | None,
    quantity: int,
):
    """Resolve a regra de desconto aplicável (most specific wins).

    - Regra específica do cliente (se houver faixa atingida) vence a global.
    - Entre faixas, vale a MAIOR min_quantity <= quantity (VOLUME model).
    """
    repo = QuantityDiscountRepository(db)
    if customer_id:
        customer_rules = await repo.list_active_for_customer_product(
            customer_id, product_id
        )
        best = _pick_tier(customer_rules, quantity)
        if best is not None:
            return best
    global_rules = await repo.list_active_global_for_product(product_id)
    return _pick_tier(global_rules, quantity)

def _pick_tier(rules, quantity):
    applicable = [r for r in rules if r.min_quantity <= quantity]
    if not applicable:
        return None
    return max(applicable, key=lambda r: r.min_quantity)