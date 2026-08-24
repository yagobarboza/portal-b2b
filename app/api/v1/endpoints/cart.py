"""Endpoints de carrinho (cliente + visão do tenant).
- GET /cart                    -> carrinho do cliente (cart:manage)
- POST /cart/items             -> adicionar item (cliente)
- PATCH /cart/items/{id}       -> alterar quantidade (cliente)
- DELETE /cart/items/{id}      -> remover item (cliente)
- GET /cart/tenant             -> carrinhos dos clientes (tenant, orders:read)
- Isolamento por tenant + propriedade + RBAC
"""
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.core.permissions import CART_MANAGE, ORDER_READ
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

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

@router.get("/tenant", response_model=list[CartRead])
async def list_tenant_carts(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_READ)),
) -> list[CartRead]:
    """Tenant: vê os carrinhos abertos de todos os clientes."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = CartRepository(db)
    carts = await repo.list_carts_by_tenant()
    return [
        CartRead(
            id=c.id, customer_id=c.customer_id,
            status=c.status.value, items=c.items,
            total=sum(i.subtotal for i in c.items),
        )
        for c in carts
    ]

@router.get("", response_model=CartRead)
async def get_cart(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CART_MANAGE)),
) -> CartRead:
    """Cliente: obtém o próprio carrinho."""
    if _is_agent(user):
        raise NotFoundError("Página não encontrada.")
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
    user: User = Depends(require_permission(CART_MANAGE)),
) -> CartItemRead:
    if _is_agent(user):
        raise NotFoundError("Página não encontrada.")
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
    user: User = Depends(require_permission(CART_MANAGE)),
) -> CartItemRead:
    if _is_agent(user):
        raise NotFoundError("Página não encontrada.")
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
    user: User = Depends(require_permission(CART_MANAGE)),
) -> None:
    if _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    customer_id = _get_customer(user)
    repo = CartRepository(db)
    cart = await repo.get_or_create_open_cart(customer_id)
    item = await repo.get_item(item_id)
    if not item or item.cart_id != cart.id:
        raise NotFoundError("Item não encontrado.")
    await repo.remove_item(item)
    await db.commit()