"""Endpoints de carrinho (Bloco 7 — seção 21).

- Adicionar/remover/alterar quantidade.
- Backend revalida produto, preço e quantidade (nunca confia no frontend).
- Isolamento por tenant + propriedade (cliente só acessa o próprio carrinho).
"""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import NotFoundError, ValidationError
from app.database.session import get_db
from app.models import User
from app.repositories.cart import CartRepository
from app.schemas.cart import CartItemAdd, CartItemRead, CartItemUpdate, CartRead
from app.services.cart_validation import validate_cart_item

router = APIRouter(prefix="/cart", tags=["Carrinho"])

def _get_customer(user: User) -> UUID:
    if not user.customer_id:
        raise ValidationError("Usuário não vinculado a um cliente.")
    return user.customer_id

@router.get("", response_model=CartRead)
async def get_cart(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CartRead:
    customer_id = _get_customer(user)
    repo = CartRepository(db)
    cart = await repo.get_or_create_open_cart(customer_id)
    total = sum(i.subtotal for i in cart.items)
    return CartRead(
        id=cart.id, customer_id=cart.customer_id,
        status=cart.status.value, items=cart.items, total=total,
    )

@router.post("/items", response_model=CartItemRead, status_code=201)
async def add_item(
    body: CartItemAdd,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CartItemRead:
    customer_id = _get_customer(user)
    product, price, _ = await validate_cart_item(
        db, body.product_id, body.quantity, customer_id
    )
    repo = CartRepository(db)
    cart = await repo.get_or_create_open_cart(customer_id)
    item = await repo.add_item(cart, product.id, body.quantity, price)
    await db.commit()
    return item

@router.patch("/items/{item_id}", response_model=CartItemRead)
async def update_quantity(
    item_id: UUID,
    body: CartItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CartItemRead:
    customer_id = _get_customer(user)
    repo = CartRepository(db)
    cart = await repo.get_or_create_open_cart(customer_id)
    item = await repo.get_item(item_id)
    if not item or item.cart_id != cart.id:
        raise NotFoundError("Item não encontrado.")
    _, price, _ = await validate_cart_item(
        db, item.product_id, body.quantity, customer_id
    )
    item = await repo.update_quantity(item, body.quantity, price)
    await db.commit()
    return item

@router.delete("/items/{item_id}", status_code=204)
async def remove_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    customer_id = _get_customer(user)
    repo = CartRepository(db)
    cart = await repo.get_or_create_open_cart(customer_id)
    item = await repo.get_item(item_id)
    if not item or item.cart_id != cart.id:
        raise NotFoundError("Item não encontrado.")
    await repo.remove_item(item)
    await db.commit()