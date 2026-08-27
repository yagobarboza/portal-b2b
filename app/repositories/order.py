"""Repositório de pedidos (checkout + aprovação/gestão do tenant)."""
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

    async def create_from_cart(
        self, cart: Cart, customer_id: UUID, notes: str | None = None
    ) -> Order:
        order = Order(
            tenant_id=self._tenant(),
            number=await self._next_number(),
            customer_id=customer_id,
            status=OrderStatus.SUBMITTED,
            total=sum(i.subtotal for i in cart.items),
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
        self.db.add(
            OrderStatusHistory(
                tenant_id=self._tenant(),
                order_id=order.id,
                from_status=None,
                to_status=OrderStatus.SUBMITTED,
                note="Pedido enviado para aprovação",
            )
        )
        cart.status = CartStatus.CHECKED_OUT
        await self.db.flush()
        return order

    async def _next_number(self) -> str:
        result = await self.db.execute(
            select(func.count(Order.id)).where(Order.tenant_id == self._tenant())
        )
        count = result.scalar() or 0
        return f"PED-{count + 1:05d}"

    async def get(self, order_id: UUID) -> Order | None:
        result = await self.db.execute(
            select(Order)
            .options(
                selectinload(Order.items),
                selectinload(Order.status_history),
            )
            .where(Order.id == order_id, Order.tenant_id == self._tenant())
        )
        return result.scalars().first()

    async def list_by_customer(
        self, customer_id: UUID, page: int = 1, page_size: int = 20
    ) -> tuple[list[Order], int]:
        base = select(Order).where(
            Order.tenant_id == self._tenant(),
            Order.customer_id == customer_id,
        )
        return await self._paginate(base, page, page_size)

    async def list_by_tenant(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Order], int]:
        """Lista pedidos de TODOS os clientes do tenant (para aprovação)."""
        base = select(Order).where(Order.tenant_id == self._tenant())
        if status:
            base = base.where(Order.status == status)
        return await self._paginate(base, page, page_size)

    async def _paginate(self, base, page: int, page_size: int):
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.options(
                selectinload(Order.items),
                selectinload(Order.status_history),
            )
            .order_by(Order.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update_status(
        self, order: Order, new_status: OrderStatus, note: str | None = None
    ) -> Order:
        old = order.status
        order.status = new_status
        history = OrderStatusHistory(
            tenant_id=self._tenant(),
            order_id=order.id,
            from_status=old,
            to_status=new_status,
            note=note,
        )
        self.db.add(history)
        # 🔒 Sincroniza a coleção em memória: a sessão usa expire_on_commit=False,
        # então sem isto o repo.get() seguinte retorna o histórico ANTIGO (stale).
        order.status_history.append(history)
        await self.db.flush()
        return order