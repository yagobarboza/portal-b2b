"""Repositório de carrinho (Bloco 7 — seção 21).

TODAS as queries filtram por tenant_id (isolamento, seção 5).
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import TenantContext
from app.models import Cart, CartItem
from app.models.enums import CartStatus

class CartRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def get_open_cart(self, customer_id: UUID) -> Cart | None:
        result = await self.db.execute(
            select(Cart)
            .options(selectinload(Cart.items))
            .where(
                Cart.tenant_id == self._tenant(),
                Cart.customer_id == customer_id,
                Cart.status == CartStatus.OPEN,
            )
        )
        return result.scalars().first()

    async def create_cart(self, customer_id: UUID) -> Cart:
        cart = Cart(
            tenant_id=self._tenant(),
            customer_id=customer_id,
            status=CartStatus.OPEN,
        )
        self.db.add(cart)
        await self.db.flush()
        return cart

    async def get_or_create_open_cart(self, customer_id: UUID) -> Cart:
        cart = await self.get_open_cart(customer_id)
        if not cart:
            cart = await self.create_cart(customer_id)
        return cart

    async def get_item(self, item_id: UUID) -> CartItem | None:
        result = await self.db.execute(
            select(CartItem).where(
                CartItem.id == item_id,
                CartItem.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def add_item(
        self,
        cart: Cart,
        product_id: UUID,
        quantity: Decimal,
        unit_price: Decimal,
    ) -> CartItem:
        result = await self.db.execute(
            select(CartItem).where(
                CartItem.cart_id == cart.id,
                CartItem.product_id == product_id,
            )
        )
        existing = result.scalars().first()
        if existing:
            existing.quantity += quantity
            existing.unit_price = unit_price
            existing.subtotal = existing.quantity * unit_price
            return existing

        item = CartItem(
            tenant_id=self._tenant(),
            cart_id=cart.id,
            product_id=product_id,
            quantity=quantity,
            unit_price=unit_price,
            subtotal=quantity * unit_price,
        )
        self.db.add(item)
        await self.db.flush()
        return item

    async def update_quantity(
        self, item: CartItem, quantity: Decimal, unit_price: Decimal
    ) -> CartItem:
        item.quantity = quantity
        item.unit_price = unit_price
        item.subtotal = quantity * unit_price
        await self.db.flush()
        return item

    async def remove_item(self, item: CartItem) -> None:
        await self.db.delete(item)
        await self.db.flush()