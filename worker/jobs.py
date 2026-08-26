"""Funções de job executadas pelo worker ARQ (background).

Regras:
- Cada job abre a PRÓPRIA sessão de banco (não usa Depends do FastAPI).
- Sessão sempre fechada no finally (evita vazamento de conexão).
- Erros viram retry automático do ARQ (max_tries definido no main.py).
"""
from uuid import UUID

from app.database.session import async_session_factory

async def send_invite_email_job(
    ctx: dict,
    *,
    to_email: str,
    invite_url: str,
    company_name: str,
    expires_hours: int,
) -> bool:
    """Envia e-mail de convite em background (não bloqueia o request).

    send_invite_email já é fail-safe (nunca lança exceção).
    """
    from app.services.email import send_invite_email

    return await send_invite_email(
        to_email=to_email,
        invite_url=invite_url,
        company_name=company_name,
        expires_hours=expires_hours,
    )

async def run_sync_job(
    ctx: dict, *, integration_id: str, entity: str = "financial"
) -> None:
    """Executa uma sincronização de ERP em background.

    Assinatura real de run_sync: (db, integration, entity, records=None).
    """
    from app.repositories.integration import IntegrationRepository
    from app.services.integration import run_sync

    async with async_session_factory() as db:
        try:
            repo = IntegrationRepository(db)
            integration = await repo.get(UUID(integration_id))
            if integration and integration.is_active:
                await run_sync(db, integration, entity)
                await db.commit()
        finally:
            await db.close()

async def send_notification_job(
    ctx: dict,
    *,
    tenant_id: str,
    user_id: str | None,
    customer_id: str | None,
    ntype: str,
    title: str,
    body: str | None = None,
) -> None:
    """Cria notificações em background (eventos de pedido/ticket/chat).

    notify_user/notify_customer exigem db (AsyncSession) — abrimos aqui.
    """
    from app.models.enums import NotificationType
    from app.services.notification import notify_customer, notify_user

    async with async_session_factory() as db:
        try:
            if user_id:
                await notify_user(
                    db, UUID(tenant_id), UUID(user_id),
                    NotificationType(ntype), title, body,
                )
            if customer_id:
                await notify_customer(
                    db, UUID(tenant_id), UUID(customer_id),
                    NotificationType(ntype), title, body,
                )
            await db.commit()
        finally:
            await db.close()