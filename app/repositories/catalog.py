"""Repositórios do catálogo (seção 38: Router → Schema → Service → Repository → DB).

TODAS as queries filtram por tenant_id (isolamento obrigatório, seção 5).

Bloco 14 — Cache Redis (seção 53):
- Leitura de catálogo (produtos/categorias) é dado de baixa volatilidade.
- Cache com TTL curto (60s) + invalidação em escrita (create/update).
- Chave inclui tenant_id (nunca vaza dados entre tenants).
- Cache é otimização: se o Redis falhar, cai para o banco (fail-open).
"""
import json
from decimal import Decimal
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import TenantContext
from app.models import Category, CustomerPrice, PriceList, Product

settings = get_settings()
_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

# TTL do cache do catálogo (segundos)
CATALOG_CACHE_TTL = int(getattr(settings, "CATALOG_CACHE_TTL", 60))

def _cache_key(tenant_id: UUID, kind: str, suffix: str = "") -> str:
    """Chave de cache sempre escopada por tenant (isolamento)."""
    return f"catalog:{tenant_id}:{kind}:{suffix}"

def _serialize_product(p: Product) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "sku": p.sku,
        "code": getattr(p, "code", None),
        "price": float(p.price) if p.price is not None else None,
        "status": p.status.value if hasattr(p.status, "value") else p.status,
        "category_id": str(p.category_id) if p.category_id else None,
    }

async def _get_cached(key: str) -> list | None:
    try:
        raw = await _redis.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 — cache nunca derruba a leitura
        return None

async def _set_cached(key: str, value: list, ttl: int = CATALOG_CACHE_TTL) -> None:
    try:
        await _redis.set(key, json.dumps(value), ex=ttl)
    except Exception:  # noqa: BLE001
        pass

async def _invalidate(tenant_id: UUID, kind: str) -> None:
    """Invalida o cache de um tipo para o tenant (escrita)."""
    try:
        pattern = f"catalog:{tenant_id}:{kind}:*"
        keys = [k async for k in _redis.scan_iter(match=pattern)]
        if keys:
            await _redis.delete(*keys)
    except Exception:  # noqa: BLE001
        pass

class CategoryRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> Category:
        obj = Category(tenant_id=self._tenant(), **data)
        self.db.add(obj)
        await self.db.flush()
        await _invalidate(self._tenant(), "categories")  # invalida cache
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
        """Lista categorias com cache Redis (seção 53)."""
        tenant = self._tenant()
        key = _cache_key(tenant, "categories")
        cached = await _get_cached(key)
        if cached is not None:
            return [Category(**c) for c in cached]

        result = await self.db.execute(
            select(Category).where(Category.tenant_id == tenant)
        )
        items = list(result.scalars().all())
        await _set_cached(
            key, [{"id": str(c.id), "name": c.name} for c in items]
        )
        return items

class ProductRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    async def create(self, data: dict) -> Product:
        obj = Product(tenant_id=self._tenant(), **data)
        self.db.add(obj)
        await self.db.flush()
        await _invalidate(self._tenant(), "products")  # invalida cache
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
        """Lista produtos ativos com cache Redis (seção 53)."""
        tenant = self._tenant()
        key = _cache_key(tenant, "products")
        cached = await _get_cached(key)
        if cached is not None:
            return [_deserialize_product(c) for c in cached]

        result = await self.db.execute(
            select(Product).where(
                Product.tenant_id == tenant,
                Product.status == "active",
            )
        )
        items = list(result.scalars().all())
        await _set_cached(key, [_serialize_product(p) for p in items])
        return items

    async def update(self, product: Product, data: dict) -> Product:
        for key, value in data.items():
            setattr(product, key, value)
        await self.db.flush()
        await _invalidate(self._tenant(), "products")  # invalida cache
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
        - SEM cache: busca com filtros é variável demais (o cache fica
          para listagens estáveis como list_all).
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

def _deserialize_product(data: dict) -> Product:
    """Reconstrói um Product a partir do cache (apenas campos serializados)."""
    from app.models import Product as P

    p = P()
    p.id = UUID(data["id"])
    p.name = data["name"]
    p.sku = data["sku"]
    p.code = data.get("code")
    p.price = Decimal(str(data["price"])) if data.get("price") is not None else None
    p.status = data["status"]
    p.category_id = UUID(data["category_id"]) if data.get("category_id") else None
    return p

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