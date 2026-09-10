"""Revalidação de itens de carrinho/pedido (seção 21).

O backend NUNCA confia nos valores enviados pelo frontend.
Produto, preço, quantidade e disponibilidade são revalidados
a partir da base de dados antes de qualquer operação.
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models import Product
from app.models.enums import ProductStatus
from app.repositories.catalog import ProductRepository
from app.services.pricing import calculate_product_price

async def validate_cart_item(
    db: AsyncSession,
    product_id: UUID,
    quantity: Decimal,
    customer_id: UUID | None,
    current_quantity: Decimal = Decimal("0"),
) -> tuple[Product, Decimal, str]:
    """Valida produto + quantidade + recalcula preço no backend.

    Retorna (product, preco_final, fonte_do_preco).

    - current_quantity: quantidade JÁ existente no carrinho para o MESMO
      produto (usada no add_item, onde as quantidades são somadas). Assim
      o total nunca estoura o estoque mesmo adicionando em múltiplas vezes.
    - O preço é calculado sobre a quantidade FINAL (current + quantity),
      aplicando o desconto por quantidade (Desconto Progressivo).
    """
    # 1. Produto existe, é do tenant e está ativo
    repo = ProductRepository(db)
    product = await repo.get(product_id)
    if not product:
        raise ValidationError("Produto não encontrado.")
    if product.status != ProductStatus.ACTIVE:
        raise ValidationError("Produto indisponível.")

    # 2. Quantidade válida
    if quantity <= 0:
        raise ValidationError("Quantidade deve ser maior que zero.")

    # 2b. Estoque disponível (seção 21) — nunca permite quantidade acima do estoque.
    # total = o que já está no carrinho + o que está sendo adicionado.
    current = (
        current_quantity
        if current_quantity and current_quantity > 0
        else Decimal("0")
    )
    total_quantity = current + quantity
    if product.stock is not None and total_quantity > product.stock:
        raise ValidationError(
            f"Estoque insuficiente para '{product.name}'. "
            f"Disponível: {product.stock}."
        )

    # 3. Preço recalculado no backend (nunca confia no frontend — seção 21).
    #    Passa a quantidade TOTAL para aplicar o desconto por quantidade.
    price, source = await calculate_product_price(
        db, product, customer_id, int(total_quantity)
    )

    return product, price, source