"""Repositórios do catálogo (seção 38: Router → Schema → Service → Repository → DB).

TODAS as queries filtram por tenant_id (isolamento obrigatório, seção 5).
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.models import Category, Product, CustomerPrice, PriceList

class CategoryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> Category:
        obj = Category(tenant_id=self._tenant(), **data)
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get(self, category_id: UUID) -> Category | None:
        result = await self.db.execute(
            select(Category).where(
                Category.id == category_id,
                Category.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list_all(self) -> list[Category]:
        result = await self.db.execute(
            select(Category).where(Category.tenant_id == self._tenant())
        )
        return list(result.scalars().all())

class ProductRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> Product:
        obj = Product(tenant_id=self._tenant(), **data)
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get(self, product_id: UUID) -> Product | None:
        result = await self.db.execute(
            select(Product).where(
                Product.id == product_id,
                Product.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def list_all(self) -> list[Product]:
        result = await self.db.execute(
            select(Product).where(Product.tenant_id == self._tenant())
        )
        return list(result.scalars().all())

    async def update(self, product: Product, data: dict) -> Product:
        for key, value in data.items():
            setattr(product, key, value)
        await self.db.flush()
        return product

    async def search(
        self,
        *,
        search: str | None = None,
        category_id: UUID | None = None,
        status: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Product], int]:
        """Busca paginada de produtos com filtros e ordenação (seção 53).

        - Filtra SEMPRE por tenant_id (isolamento, seção 5).
        - Ordenação apenas por colunas da whitelist (anti-injeção).
        - Retorna (itens, total).
        """
        tenant = self._tenant()
        stmt = select(Product).where(Product.tenant_id == tenant)

        # Filtros
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                or_(
                    Product.name.ilike(like),
                    Product.sku.ilike(like),
                    Product.code.ilike(like),
                )
            )
        if category_id:
            stmt = stmt.where(Product.category_id == category_id)
        if status:
            stmt = stmt.where(Product.status == status)
        if min_price is not None:
            stmt = stmt.where(Product.price >= min_price)
        if max_price is not None:
            stmt = stmt.where(Product.price <= max_price)

        # Total (para paginação)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        # Ordenação (whitelist de colunas — anti-injeção, seção 53)
        sort_col = {
            "name": Product.name,
            "sku": Product.sku,
            "price": Product.price,
            "created_at": Product.created_at,
            "status": Product.status,
        }[sort_by]
        stmt = stmt.order_by(
            sort_col.asc() if sort_dir == "asc" else sort_col.desc()
        )

        # Paginação
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())
        return items, total

from app.models import CustomerPrice, PriceList

class PriceListRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> PriceList:
        obj = PriceList(tenant_id=self._tenant(), **data)
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get(self, price_list_id: UUID) -> PriceList | None:
        result = await self.db.execute(
            select(PriceList).where(
                PriceList.id == price_list_id,
                PriceList.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

class CustomerPriceRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> CustomerPrice:
        obj = CustomerPrice(tenant_id=self._tenant(), **data)
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get_for_product(self, customer_id: UUID, product_id: UUID) -> CustomerPrice | None:
        """Busca o preço específico de um cliente para um produto (seção 17).

        Filtra por tenant_id (isolamento) e valida que o cliente pertence
        ao mesmo tenant (evita IDOR/BOLA).
        """
        result = await self.db.execute(
            select(CustomerPrice).where(
                CustomerPrice.customer_id == customer_id,
                CustomerPrice.product_id == product_id,
                CustomerPrice.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()