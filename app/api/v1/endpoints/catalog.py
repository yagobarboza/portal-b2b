"""Endpoints do catálogo (seções 16 e 17 do doc).

- Categorias: CRUD (Admin/Vendedor).
- Produtos: CRUD + busca/filtros/ordenação/paginação (Admin/Vendedor).
- Tabelas de preço e preço por cliente (seção 17).
- Cotação de preço: o backend recalcula o preço final (nunca confia no frontend).
- Isolamento por tenant em todas as queries (seção 5).
- RBAC: require_permission (seção 13).
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_permission
from app.core.exceptions import NotFoundError
from app.core.permissions import CATALOG_MANAGE
from app.database.session import get_db
from app.models import User
from app.repositories.catalog import (
    CategoryRepository,
    CustomerPriceRepository,
    PriceListRepository,
    ProductRepository,
)
from app.schemas.catalog import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    CustomerPriceCreate,
    CustomerPriceRead,
    PriceListCreate,
    PriceListRead,
    PriceQuote,
    ProductCreate,
    ProductListParams,
    ProductPage,
    ProductRead,
    ProductUpdate,
)
from app.services.audit import record_audit
from app.services.pricing import calculate_product_price
from app.services.product_images import attach_product_images

router = APIRouter(prefix="/catalog", tags=["Catálogo"])

# ---------- Categorias ----------
@router.post("/categories", response_model=CategoryRead, status_code=201)
async def create_category(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CATALOG_MANAGE)),

) -> CategoryRead:
    repo = CategoryRepository(db)
    category = await repo.create(body.model_dump())
    await record_audit(
        db, action="create", entity="category",
        entity_id=category.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return category

@router.get("/categories", response_model=list[CategoryRead])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CategoryRead]:
    repo = CategoryRepository(db)
    return await repo.list_all()

@router.get("/categories/{category_id}", response_model=CategoryRead)
async def get_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CategoryRead:
    repo = CategoryRepository(db)
    category = await repo.get(category_id)
    if not category:
        raise NotFoundError("Categoria não encontrada.")
    return category

@router.patch("/categories/{category_id}", response_model=CategoryRead)
async def update_category(
    category_id: UUID,
    body: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CATALOG_MANAGE)),

) -> CategoryRead:
    repo = CategoryRepository(db)
    category = await repo.get(category_id)
    if not category:
        raise NotFoundError("Categoria não encontrada.")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    for key, value in data.items():
        setattr(category, key, value)
    await record_audit(
        db, action="update", entity="category",
        entity_id=category.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return category

# ---------- Produtos ----------
@router.post("/products", response_model=ProductRead, status_code=201)
async def create_product(
    body: ProductCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CATALOG_MANAGE)),

) -> ProductRead:
    repo = ProductRepository(db)
    product = await repo.create(body.model_dump())
    await record_audit(
        db, action="create", entity="product",
        entity_id=product.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return product

@router.get("/products", response_model=ProductPage)
async def list_products(
    params: ProductListParams = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProductPage:
    """Lista produtos com busca, filtros, ordenação e paginação (seção 53)."""
    repo = ProductRepository(db)
    items, total = await repo.search(
        search=params.search,
        category_id=params.category_id,
        status=params.status,
        min_price=params.min_price,
        max_price=params.max_price,
        sort_by=params.sort_by,
        sort_dir=params.sort_dir,
        page=params.page,
        page_size=params.page_size,
    )
    pages = (total + params.page_size - 1) // params.page_size

    # Resolve a URL da imagem de cada produto em UMA query (R2 signed URL)
    image_urls = await attach_product_images(db, items)
    enriched = [
        ProductRead.model_validate(p).model_copy(
            update={"image_url": image_urls.get(p.id)}
        )
        for p in items
    ]

    return ProductPage(
        items=enriched,
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=pages,
    )

@router.get("/products/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProductRead:
    repo = ProductRepository(db)
    product = await repo.get(product_id)
    if not product:
        raise NotFoundError("Produto não encontrado.")

    image_urls = await attach_product_images(db, [product])
    return ProductRead.model_validate(product).model_copy(
        update={"image_url": image_urls.get(product.id)}
    )

@router.patch("/products/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: UUID,
    body: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CATALOG_MANAGE)),

) -> ProductRead:
    repo = ProductRepository(db)
    product = await repo.get(product_id)
    if not product:
        raise NotFoundError("Produto não encontrado.")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    for key, value in data.items():
        setattr(product, key, value)
    await record_audit(
        db, action="update", entity="product",
        entity_id=product.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return product

# ---------- Tabelas de preço (seção 17) ----------
@router.post("/price-lists", response_model=PriceListRead, status_code=201)
async def create_price_list(
    body: PriceListCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CATALOG_MANAGE)),

) -> PriceListRead:
    repo = PriceListRepository(db)
    price_list = await repo.create(body.model_dump())
    await record_audit(
        db, action="create", entity="price_list",
        entity_id=price_list.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return price_list

# ---------- Preço por cliente (seção 17) ----------
@router.post("/customer-prices", response_model=CustomerPriceRead, status_code=201)
async def create_customer_price(
    body: CustomerPriceCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(CATALOG_MANAGE)),

) -> CustomerPriceRead:
    repo = CustomerPriceRepository(db)
    cp = await repo.create(body.model_dump())
    await record_audit(
        db, action="create", entity="customer_price",
        entity_id=cp.id, user_id=user.id, tenant_id=user.tenant_id,
    )
    await db.commit()
    return cp

# ---------- Cotação de preço (seção 17) ----------
@router.get("/products/{product_id}/quote", response_model=PriceQuote)
async def quote_product_price(
    product_id: UUID,
    customer_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PriceQuote:
    """Calcula o preço final de um produto para um cliente (seção 17).

    O backend recalcula o preço — nunca confia no frontend.
    Prioridade: preço do cliente > preço padrão do produto.
    """
    repo = ProductRepository(db)
    product = await repo.get(product_id)
    if not product:
        raise NotFoundError("Produto não encontrado.")
    final_price, source = await calculate_product_price(db, product, customer_id)
    return PriceQuote(
        product_id=product.id,
        sku=product.sku,
        name=product.name,
        base_price=product.price,
        customer_price=final_price if source == "customer" else None,
        final_price=final_price,
        price_source=source,
    )