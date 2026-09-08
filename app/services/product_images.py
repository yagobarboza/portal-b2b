"""Serviço de imagens de produtos.

Associa o PRIMEIRO arquivo (File) de owner_type=product a cada produto
e monta uma URL PÚBLICA PERMANENTE do Cloudflare R2.

- 1 query em lote (evita N+1 na listagem de produtos).
- Filtra por tenant_id (isolamento obrigatório, seção 5).
- URL permanente (não expira) — exige R2_PUBLIC_BASE_URL configurado.
"""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import TenantContext
from app.models import File
from app.models.enums import FileOwnerType

logger = logging.getLogger("product_images")

settings = get_settings()

def _public_url(storage_key: str) -> str | None:
    """Monta a URL pública permanente do R2.

    Exige R2_PUBLIC_BASE_URL no .env (ex.: https://cdn.seusite.com.br).
    Se não configurado, retorna None (produto aparece sem imagem) e loga
    um aviso claro para o operador.
    """
    base = (settings.R2_PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        logger.warning(
            "R2_PUBLIC_BASE_URL não configurado — imagens de produto não "
            "serão exibidas. Configure o .env com a URL pública do bucket."
        )
        return None
    return f"{base}/{storage_key.lstrip('/')}"

def _current_tenant_id() -> UUID | None:
    """Retorna o tenant_id do contexto atual de forma segura.

    Evita que a ausência do TenantContext (ex.: chamadas fora de um
    request autenticado) quebre a query com exceção não tratada.
    """
    try:
        return TenantContext.tenant_id()
    except Exception:  # noqa: BLE001 — contexto ausente não deve derrubar
        logger.warning("TenantContext não definido ao resolver imagens.")
        return None

async def attach_product_images(
    db: AsyncSession,
    products: list,
) -> dict[UUID, str | None]:
    """Retorna {product_id: image_url} para os produtos informados.

    Se o tenant do contexto não estiver disponível, retorna um dicionário
    vazio (nenhum produto recebe imagem) em vez de lançar erro.
    """
    if not products:
        return {}

    tenant_id = _current_tenant_id()
    if tenant_id is None:
        return {}

    product_ids = [p.id for p in products]

    # Busca TODOS os arquivos de produto do tenant em UMA query
    result = await db.execute(
        select(File)
        .where(
            File.tenant_id == tenant_id,
            File.owner_type == FileOwnerType.PRODUCT,
            File.owner_id.in_(product_ids),
        )
        .order_by(File.created_at.asc())
    )
    files = list(result.scalars().all())

    urls: dict[UUID, str | None] = {pid: None for pid in product_ids}
    seen: set[UUID] = set()

    for f in files:
        if f.owner_id in seen:
            continue  # apenas o PRIMEIRO arquivo de cada produto
        seen.add(f.owner_id)
        urls[f.owner_id] = _public_url(f.storage_key)

    return urls