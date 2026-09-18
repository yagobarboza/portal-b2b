"""Endpoints de Pedidos (checkout + aprovação/gestão do tenant).

- POST /orders                    -> checkout (cliente) — orders:create
- GET /orders                     -> listar pedidos do cliente (orders:read)
- GET /orders/tenant              -> listar pedidos de todos os clientes (tenant)
- GET /orders/{id}                -> detalhe
- POST /orders/{id}/cancel        -> cliente cancela o próprio pedido
- PATCH /orders/{id}/status       -> aprovar/rejeitar (tenant) — orders:manage
- Isolamento por tenant + propriedade + RBAC
"""
from datetime import datetime
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
from app.repositories.company import CompanyRepository
from app.repositories.order import OrderRepository
from app.schemas.order import OrderPage, OrderRead, OrderStatusUpdate
from app.services.audit import record_audit
from app.services.cart_validation import validate_cart_item

router = APIRouter(prefix="/orders", tags=["Pedidos"])

# ✅ Estados em que o CLIENTE ainda pode cancelar o próprio pedido.
# (Depois de enviado/em trânsito/concluído, não pode mais.)
CANCELLABLE_STATUSES = {
    OrderStatus.SUBMITTED,
    OrderStatus.UNDER_REVIEW,
    OrderStatus.APPROVED,
}


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

    # ✅ Valida as regras de compra da empresa (valor e/ou quantidade mínima).
    tenant_id = user.tenant_id
    if tenant_id:
        company = await CompanyRepository(db).get(tenant_id)
        if company:
            total_cart = sum(i.subtotal for i in cart.items)
            total_qty = sum(i.quantity for i in cart.items)
            if company.min_order_value is not None and total_cart < company.min_order_value:
                raise ValidationError(
                    f"Valor mínimo de compra não atingido: o pedido deve ser "
                    f"de pelo menos R$ {company.min_order_value:,.2f}."
                )
            if company.min_order_quantity is not None and total_qty < company.min_order_quantity:
                raise ValidationError(
                    f"Quantidade mínima de compra não atingida: são necessárias "
                    f"pelo menos {company.min_order_quantity} unidade(s)."
                )

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
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_MANAGE)),
) -> OrderPage:
    """Tenant: lista pedidos de TODOS os clientes (para aprovação).

    Filtros opcionais:
      - status: status exato do pedido.
      - date_from / date_to: intervalo pela data de CRIAÇÃO (ISO 8601 UTC).
        Ex.: ?date_from=2026-09-10T03:00:00Z&date_to=2026-09-11T02:59:59Z
        devolve os pedidos do dia 10/09 no fuso de São Paulo.
      - Quando omitidos, mantém o comportamento anterior (todos os pedidos),
        preservando a retrocompatibilidade.
    """
    if not _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    repo = OrderRepository(db)
    items, total = await repo.list_by_tenant(
        status, date_from, date_to, page, page_size
    )
    pages = (total + page_size - 1) // page_size
    return OrderPage(
        items=items, total=total, page=page, page_size=page_size, pages=pages
    )


@router.get("", response_model=OrderPage)
async def list_orders(
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    search: str | None = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_READ)),
) -> OrderPage:
    """Cliente: lista os próprios pedidos com filtros aplicados NO BANCO.

    Os filtros valem para TODOS os pedidos do cliente (não só da página atual):
      - status: status exato do pedido.
      - date_from / date_to: intervalo pela data de CRIAÇÃO (ISO 8601 UTC).
      - search: busca parcial (case-insensitive) pelo número do pedido.
      - sort_by: 'created_at' | 'number' | 'total'.
      - sort_dir: 'asc' | 'desc'.
    """
    if _is_agent(user):
        raise NotFoundError("Página não encontrada.")
    customer_id = _get_customer(user)
    repo = OrderRepository(db)
    items, total = await repo.list_by_customer(
        customer_id,
        page=page,
        page_size=page_size,
        status=status,
        date_from=date_from,
        date_to=date_to,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
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


@router.post("/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(ORDER_CREATE)),
) -> OrderRead:
    """Cliente: cancela o próprio pedido (se ainda cancelável).

    Registra o histórico de status (from_status -> cancelled) e dispara o
    evento para integrações futuras (webhooks/API em tempo real) via o mesmo
    fluxo de `update_status` + audit.
    """
    if _is_agent(user):
        raise ForbiddenError("Acesso negado.")
    customer_id = _get_customer(user)
    repo = OrderRepository(db)
    order = await repo.get(order_id)
    if not order or order.customer_id != customer_id:
        raise NotFoundError("Pedido não encontrado.")
    if order.status not in CANCELLABLE_STATUSES:
        raise ValidationError(
            "Este pedido não pode ser cancelado no estado atual."
        )
    order = await repo.update_status(
        order, OrderStatus.CANCELLED, "Pedido cancelado pelo cliente"
    )
    await record_audit(
        db, action="cancel", entity="order",
        entity_id=order.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return await repo.get(order.id)


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