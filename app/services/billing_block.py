"""Regra de negócio: inativação por não pagamento.

Se a empresa tem uma mensalidade VENCIDA (overdue) há mais de
BILLING_GRACE_DAYS (30 dias) após o vencimento, ela é inativada.

⚠️ Este serviço deve ser executado por um JOB PERIÓDICO (diário) —
ex.: scheduler dedicado (worker/scheduler.py).

Idempotente e deduplicado:
- Só inativa empresas com status ATIVO (pula as já inativas);
- Uma empresa com várias mensalidades vencidas é inativada UMA única vez;
- Retorna a lista de IDs das empresas inativadas (auditoria/notificação).
"""
import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.enums import CompanyStatus
from app.repositories.billing import BillingRepository
from app.repositories.company import CompanyRepository

logger = logging.getLogger("billing_block")

async def block_overdue_companies(db: AsyncSession) -> list[UUID]:
    """Inativa empresas com mensalidade vencida além do período de tolerância.

    Retorna a lista de IDs das empresas inativadas (para auditoria/notificação).
    """
    settings = get_settings()
    grace_days = settings.BILLING_GRACE_DAYS
    cutoff = date.today() - timedelta(days=grace_days)

    billing_repo = BillingRepository(db)
    company_repo = CompanyRepository(db)

    # Mensalidades vencidas com vencimento <= cutoff (30+ dias atrás).
    overdue_charges = await billing_repo.list_overdue_mensalidade_before(cutoff)

    # Deduplica por empresa (uma empresa pode ter várias mensalidades vencidas).
    tenant_ids: set[UUID] = {charge.tenant_id for charge in overdue_charges}

    blocked: list[UUID] = []
    for tenant_id in tenant_ids:
        company = await company_repo.get(tenant_id)
        if not company or company.status != CompanyStatus.ACTIVE:
            continue  # empresa já inativa ou inexistente — pula (idempotente)

        await company_repo.set_status(company, CompanyStatus.INACTIVE)
        blocked.append(company.id)
        logger.warning(
            "Empresa inativada por não pagamento: %s (vencimento <= %s)",
            company.id,
            cutoff.isoformat(),
        )

    if blocked:
        await db.commit()
        logger.info("Bloqueio por não pagamento: %d empresa(s) inativada(s).", len(blocked))

    return blocked
