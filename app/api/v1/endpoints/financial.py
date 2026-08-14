"""Endpoints do módulo financeiro (Bloco 10 — seção 27).

- Somente leitura (princípio 5): a origem oficial é o ERP/processo de
  integração; o portal apenas consulta.
- Cliente: vê as próprias contas (abertas, pagas, vencidas).
- Empresa: vê as contas do tenant.
- Isolamento por tenant + propriedade em todas as rotas.
"""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db
from app.models import User
from app.models.enums import FinancialAccountStatus
from app.repositories.financial import FinancialRepository
from app.schemas.financial import (
    FinancialAccountDetailRead,
    FinancialAccountPage,
    FinancialAccountRead,
    FinancialPaymentRead,
)

router = APIRouter(prefix="/financial", tags=["Financeiro"])

def _is_agent(user: User) -> bool:
    return user.is_super_admin or user.customer_id is None

async def _get_account_for_user(db: AsyncSession, user: User, account_id: UUID):
    """Valida tenant + propriedade/permissão (seções 14, 25).

    Mensagem genérica: quem não tem acesso recebe 404 (anti-vazamento).
    """
    repo = FinancialRepository(db)
    account = await repo.get(account_id)
    if not account:
        raise NotFoundError("Conta não encontrada.")
    if user.is_super_admin:
        return account
    if user.customer_id:
        if account.customer_id != user.customer_id:
            raise NotFoundError("Conta não encontrada.")
    else:
        if account.tenant_id != user.tenant_id:
            raise NotFoundError("Conta não encontrada.")
    return account

async def _list(
    db: AsyncSession,
    user: User,
    status: FinancialAccountStatus,
    page: int,
    page_size: int,
) -> FinancialAccountPage:
    repo = FinancialRepository(db)
    customer_id = None if _is_agent(user) else user.customer_id
    items, total = await repo.list_accounts(customer_id, status, page, page_size)
    return FinancialAccountPage(items=items, total=total, page=page, page_size=page_size)

@router.get("/accounts/open", response_model=FinancialAccountPage)
async def list_open(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FinancialAccountPage:
    """Contas em aberto (seção 27)."""
    return await _list(db, user, FinancialAccountStatus.OPEN, page, page_size)

@router.get("/accounts/paid", response_model=FinancialAccountPage)
async def list_paid(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FinancialAccountPage:
    """Contas pagas (seção 27)."""
    return await _list(db, user, FinancialAccountStatus.PAID, page, page_size)

@router.get("/accounts/overdue", response_model=FinancialAccountPage)
async def list_overdue(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FinancialAccountPage:
    """Contas vencidas (seção 27)."""
    return await _list(db, user, FinancialAccountStatus.OVERDUE, page, page_size)

@router.get("/accounts/{account_id}", response_model=FinancialAccountDetailRead)
async def get_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FinancialAccountDetailRead:
    """Detalhe da conta + dias de atraso + pagamentos (seção 27)."""
    account = await _get_account_for_user(db, user, account_id)
    repo = FinancialRepository(db)
    payments = await repo.list_payments(account.id)
    days_overdue = 0
    if account.status == FinancialAccountStatus.OVERDUE:
        days_overdue = max(0, (date.today() - account.due_date.date()).days)
    data = FinancialAccountRead.model_validate(account).model_dump()
    return FinancialAccountDetailRead(
        **data,
        days_overdue=days_overdue,
        payments=[FinancialPaymentRead.model_validate(p) for p in payments],
    )