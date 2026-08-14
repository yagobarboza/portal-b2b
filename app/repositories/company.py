from app.models import Company
from app.repositories.base import BaseRepository

class CompanyRepository(BaseRepository):
    """Company É o tenant (raiz). Não tem tenant_id próprio."""

    model = Company
    tenant_scoped = False