"""Repositório de cobranças (BillingCharge) — isolamento por tenant."""
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.models.billing import BillingCharge
from app.models.enums import BillingStatus

# Limites defensivos de paginação (anti-DoS).
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class BillingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _tenant(self) -> UUID | None:
        return TenantContext.tenant_id()

    @staticmethod
    def _clamp_page(page: int, page_size: int) -> tuple[int, int]:
        page = max(1, page)
        page_size = min(max(1, page_size), MAX_PAGE_SIZE)
        return page, page_size

    async def create(self, *, tenant_id: UUID, **kwargs) -> BillingCharge:
        charge = BillingCharge(tenant_id=tenant_id, **kwargs)
        self.db.add(charge)
        await self.db.flush()
        return charge

    async def get(self, charge_id: UUID) -> BillingCharge | None:
        """Busca UMA cobrança SEMPRE filtrada pelo tenant da sessão."""
        result = await self.db.execute(
            select(BillingCharge).where(
                BillingCharge.id == charge_id,
                BillingCharge.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def get_by_asaas_payment_id(self, asaas_payment_id: str) -> BillingCharge | None:
        """Busca por ID do Asaas (uso interno com sessão). Filtra por tenant."""
        result = await self.db.execute(
            select(BillingCharge).where(
                BillingCharge.asaas_payment_id == asaas_payment_id,
                BillingCharge.tenant_id == self._tenant(),
            )
        )
        return result.scalars().first()

    async def get_by_asaas_payment_id_global(
        self, asaas_payment_id: str
    ) -> BillingCharge | None:
        """Busca por ID do Asaas SEM filtro de tenant (uso interno/webhook).

        O webhook do Asaas não tem sessão/tenant, então NÃO pode filtrar
        por TenantContext. A segurança é feita pelo mapeamento do ID
        (o Asaas só envia IDs de cobranças que existem na conta).
        """
        result = await self.db.execute(
            select(BillingCharge).where(
                BillingCharge.asaas_payment_id == asaas_payment_id,
            )
        )
        return result.scalars().first()

    async def get_by_asaas_subscription_id(
        self, asaas_subscription_id: str
    ) -> BillingCharge | None:
        """Busca a cobrança 'raiz' de uma assinatura (mensalidade).

        Usado para localizar a empresa dona da assinatura a partir do
        webhook de assinatura do Asaas. NÃO filtra por tenant (o webhook
        não tem sessão) — a segurança é feita pelo mapeamento do ID.
        """
        result = await self.db.execute(
            select(BillingCharge).where(
                BillingCharge.asaas_subscription_id == asaas_subscription_id,
            )
        )
        return result.scalars().first()

    async def get_active_subscription(self, tenant_id: UUID) -> BillingCharge | None:
        """Retorna a assinatura ativa (mensalidade) de uma empresa, se houver.

        Considera ativa uma mensalidade com `asaas_subscription_id` preenchido
        e status que NÃO seja cancelado/reembolsado. Usado para impedir a
        criação de assinaturas duplicadas no Asaas.
        """
        result = await self.db.execute(
            select(BillingCharge)
            .where(
                BillingCharge.tenant_id == tenant_id,
                BillingCharge.type == "mensalidade",
                BillingCharge.asaas_subscription_id.isnot(None),
                BillingCharge.status.notin_(
                    [BillingStatus.CANCELLED, BillingStatus.REFUNDED]
                ),
            )
            .order_by(BillingCharge.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def update_status(
        self, charge: BillingCharge, status: BillingStatus
    ) -> None:
        """Atualiza o status de uma cobrança (e paid_at quando paga).

        Usado pelo webhook do Asaas para refletir o status financeiro.
        """
        charge.status = status
        if status == BillingStatus.PAID and charge.paid_at is None:
            charge.paid_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def list_overdue_mensalidade_before(
        self, cutoff: date
    ) -> list[BillingCharge]:
        """Mensalidades VENCIDAS (overdue) com vencimento <= cutoff.

        Usado pela regra de inativação por não pagamento (30 dias após
        o vencimento da última mensalidade não paga).
        """
        result = await self.db.execute(
            select(BillingCharge).where(
                BillingCharge.type == "mensalidade",
                BillingCharge.status == BillingStatus.OVERDUE,
                BillingCharge.due_date <= cutoff,
            )
        )
        return list(result.scalars().all())

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        status: BillingStatus | None = None,
        type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BillingCharge], int]:
        """Admin da empresa: lista as cobranças do SEU tenant (com filtros)."""
        page, page_size = self._clamp_page(page, page_size)
        base = select(BillingCharge).where(BillingCharge.tenant_id == tenant_id)
        if status:
            base = base.where(BillingCharge.status == status)
        if type:
            base = base.where(BillingCharge.type == type)
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(BillingCharge.due_date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_all(
        self,
        *,
        status: BillingStatus | None = None,
        type: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BillingCharge], int]:
        """Super Admin: lista cobranças de TODAS as empresas (com filtros).

        O `search` filtra por external_reference (rastreio). Usa `.contains()`,
        que faz o escape de caracteres curinga (%, _) automaticamente —
        sem risco de injeção de padrão LIKE e sem escape manual frágil.
        """
        page, page_size = self._clamp_page(page, page_size)
        base = select(BillingCharge)
        if status:
            base = base.where(BillingCharge.status == status)
        if type:
            base = base.where(BillingCharge.type == type)
        if search:
            base = base.where(BillingCharge.external_reference.contains(search))
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar() or 0
        result = await self.db.execute(
            base.order_by(BillingCharge.due_date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total