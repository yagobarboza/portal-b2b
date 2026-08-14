"""Repositório de pedidos (Bloco 7 — seção 22).

- Criação de pedido a partir do carrinho (checkout).
- Histórico de alterações de status.
- Isolamento por tenant em todas as queries.
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import TenantContext
from app.models import Cart, Order, OrderItem, OrderStatusHistory
from app.models.enums import CartStatus, OrderStatus

class OrderRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def next_number(self) -> str:
        result = await self.db.execute(
            select(func.count(Order.id)).where(Order.tenant_id == self._tenant())
        )
        count = result.scalar() or 0
        return f"{count + 1:06d}"

    async def create_from_cart(
        self, cart: Cart, customer_id: UUID, notes: str | None = None
    ) -> Order:
        number = await self.next_number()
        total = sum(i.subtotal for i in cart.items)

        order = Order(
            tenant_id=self._tenant(),
            number=number,
            customer_id=customer_id,
            status=OrderStatus.SUBMITTED,
            total=total,
            notes=notes,
        )
        self.db.add(order)
        await self.db.flush()

        for item in cart.items:
            self.db.add(
                OrderItem(
                    tenant_id=self._tenant(),
                    order_id=order.id,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    subtotal=item.subtotal,
                )
            )

        # Histórico de status (seção 22)
        self.db.add(
            OrderStatusHistory(
                tenant_id=self._tenant(),
                order_id=order.id,
                from_status=None,
                to_status=OrderStatus.SUBMITTED,
                note="Pedido enviado pelo cliente",
            )
        )

        # Carrinho finalizado
        cart.status = CartStatus.CHECKED_OUT
        await self.db.flush()
        return order

    async def get(self, order_id: UUID) -> Order | None:
        result = await self.db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.status_history))
            .where(
                Order.id == order_id,
                Order.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list_by_customer(self, customer_id: UUID) -> list[Order]:
        result = await self.db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.status_history))
            .where(
                Order.tenant_id == self._tenant(),
                Order.customer_id == customer_id,
            )
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())