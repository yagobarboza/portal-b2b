"""Endpoints de pedidos (Bloco 7 — seção 22).

- Checkout do carrinho: cria pedido + histórico de status.
- Revalidação de preço/quantidade no backend antes de criar.
- Isolamento por tenant + propriedade (cliente só acessa os próprios pedidos).
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import NotFoundError, ValidationError
from app.database.session import get_db
from app.models import User
from app.repositories.cart import CartRepository
from app.repositories.order import OrderRepository
from app.schemas.order import OrderCreate, OrderRead
from app.services.audit import record_audit
from app.services.cart_validation import validate_cart_item

router = APIRouter(prefix="/orders", tags=["Pedidos"])

def _get_customer(user: User) -> UUID:
    if not user.customer_id:
        raise ValidationError("Usuário não vinculado a um cliente.")
    return user.customer_id

@router.post("", response_model=OrderRead, status_code=201)
async def checkout(
    body: OrderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderRead:
    """Checkout: cria pedido a partir do carrinho (seção 22)."""
    customer_id = _get_customer(user)
    cart_repo = CartRepository(db)
    cart = await cart_repo.get_open_cart(customer_id)
    if not cart or not cart.items:
        raise ValidationError("Carrinho vazio.")

    # Revalida todos os itens no backend (seção 21)
    for item in cart.items:
        _, price, _ = await validate_cart_item(
            db, item.product_id, item.quantity, customer_id
        )
        if price != item.unit_price:
            item.unit_price = price
            item.subtotal = item.quantity * price

    order_repo = OrderRepository(db)
    order = await order_repo.create_from_cart(cart, customer_id, body.notes)
    await record_audit(
        db, action="create", entity="order",
        entity_id=order.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()

    # Re-busca com eager load (items + status_history) para serialização
    order = await order_repo.get(order.id)
    return order

@router.get("", response_model=list[OrderRead])
async def list_orders(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[OrderRead]:
    customer_id = _get_customer(user)
    repo = OrderRepository(db)
    return await repo.list_by_customer(customer_id)

@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderRead:
    customer_id = _get_customer(user)
    repo = OrderRepository(db)
    order = await repo.get(order_id)
    if not order or order.customer_id != customer_id:
        raise NotFoundError("Pedido não encontrado.")
    return order