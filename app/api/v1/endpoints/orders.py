"""Endpoints de Pedidos (checkout + aprovação/gestão do tenant).
- POST /orders                    -> checkout (cliente) — orders:create
- GET /orders                     -> listar pedidos do cliente (orders:read)
- GET /orders/tenant              -> listar pedidos de todos os clientes (tenant)
- GET /orders/{id}                -> detalhe
- PATCH /orders/{id}/status       -> aprovar/rejeitar (tenant) — orders:manage
- Isolamento por tenant + propriedade + RBAC
"""
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user, require_permission
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.permissions import ORDER_CREATE, ORDER_MANAGE, ORDER_READ
from app.database.session import get_db
from app.models import User
from app.models.enums import OrderStatus
from app.repositories.cart import CartRepository
from app.repositories.order import OrderRepository
from app.schemas.order import OrderPage, OrderRead, OrderStatusUpdate
from app.services.audit import record_audit
from app.services.cart_validation import validate_cart_item

router = APIRouter(prefix="/orders", tags=["Pedidos"])

def _get_customer(user: User) -> UUID:
    if not user.customer_id:
        raise ValidationError("Usuário não vinculado a um cliente.")
    return user.customer_id

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

@router.post("", response_model=OrderRead, status_code=201)
async def checkout(
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_CREATE)),
) -> OrderRead:
    """Checkout: cria pedido a partir do carrinho (cliente)."""
    if _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    customer_id = _get_customer(user)
    cart_repo = CartRepository(db)
    cart = await cart_repo.get_open_cart(customer_id)
    if not cart or not cart.items:
        raise ValidationError("Carrinho vazio.")

    # Revalida todos os itens no backend (nunca confia no frontend)
    for item in cart.items:
        _, price, _ = await validate_cart_item(
            db, item.product_id, item.quantity, customer_id
        )
        if price != item.unit_price:
            item.unit_price = price
            item.subtotal = item.quantity * price

    notes = (body or {}).get("notes") if body else None
    order_repo = OrderRepository(db)
    order = await order_repo.create_from_cart(cart, customer_id, notes)
    await record_audit(
        db, action="create", entity="order",
        entity_id=order.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    order = await order_repo.get(order.id)
    return order

@router.get("/tenant", response_model=OrderPage)
async def list_tenant_orders(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_MANAGE)),
) -> OrderPage:
    """Tenant: lista pedidos de TODOS os clientes (para aprovação)."""
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = OrderRepository(db)
    items, total = await repo.list_by_tenant(status, page, page_size)
    pages = (total + page_size - 1) // page_size
    return OrderPage(
        items=items, total=total, page=page, page_size=page_size, pages=pages
    )

@router.get("", response_model=OrderPage)
async def list_orders(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_READ)),
) -> OrderPage:
    """Cliente: lista os próprios pedidos."""
    if _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    customer_id = _get_customer(user)
    repo = OrderRepository(db)
    items, total = await repo.list_by_customer(customer_id, page, page_size)
    pages = (total + page_size - 1) // page_size
    return OrderPage(
        items=items, total=total, page=page, page_size=page_size, pages=pages
    )

@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OrderRead:
    """Detalhe do pedido (cliente vê o próprio; tenant vê os do tenant)."""
    repo = OrderRepository(db)
    order = await repo.get(order_id)
    if not order:
        raise NotFoundError("Pedido não encontrado.")
    if user.customer_id:
        if order.customer_id != user.customer_id:
            raise NotFoundError("Pedido não encontrado.")
    elif order.tenant_id != user.tenant_id:
        raise NotFoundError("Pedido não encontrado.")
    return order

@router.patch("/{order_id}/status", response_model=OrderRead)
async def update_order_status(
    order_id: UUID,
    body: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_MANAGE)),
) -> OrderRead:
    """Tenant: aprova/rejeita/processa um pedido (registra histórico)."""
    if not _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    repo = OrderRepository(db)
    order = await repo.get(order_id)
    if not order:
        raise NotFoundError("Pedido não encontrado.")

    try:
        new_status = OrderStatus(body.status)
    except ValueError:
        raise ValidationError("Status inválido.")

    order = await repo.update_status(order, new_status, body.note)
    await record_audit(
        db, action="update", entity="order",
        entity_id=order.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return await repo.get(order.id)