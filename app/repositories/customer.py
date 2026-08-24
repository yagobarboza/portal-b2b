"""Repositório de Clientes (CRUD + importação)."""
from uuid import UUID
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.context import TenantContext
from app.models import Customer
from app.models.enums import CustomerStatus

class CustomerRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> Customer:
        customer = Customer(
            tenant_id=self._tenant(),
            name=data["name"],
            email=data.get("email"),
            phone=data.get("phone"),
            document=data.get("document"),
            status=CustomerStatus.ACTIVE,
        )
        self.db.add(customer)
        await self.db.flush()
        return customer

    async def get(self, customer_id: UUID) -> Customer | None:
        result = await self.db.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def get_by_email(self, email: str) -> Customer | None:
        result = await self.db.execute(
            select(Customer).where(
                Customer.email == email,
                Customer.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list(
        self,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Customer], int]:
        base = select(Customer).where(Customer.tenant_id == self._tenant())
        if search:
            like = f"%{search}%"
            base = base.where(
                or_(
                    Customer.name.ilike(like),
                    Customer.email.ilike(like),
                    Customer.document.ilike(like),
                )
            )
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(Customer.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update(self, customer: Customer, data: dict) -> Customer:
        for key, value in data.items():
            if value is not None:
                setattr(customer, key, value)
        await self.db.flush()
        return customer

    async def delete(self, customer: Customer) -> None:
        # Soft delete: marca como inativo
        customer.status = CustomerStatus.INACTIVE
        await self.db.flush()