from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth, cart, catalog, chat, company, files, financial, health,
    integrations, lgpd, notifications, orders, tickets, webhooks, invitations
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(lgpd.router)
api_router.include_router(catalog.router)
api_router.include_router(files.router)
api_router.include_router(cart.router)
api_router.include_router(orders.router)
api_router.include_router(chat.router)
api_router.include_router(tickets.router)
api_router.include_router(financial.router)
api_router.include_router(integrations.router)
api_router.include_router(webhooks.router)
api_router.include_router(notifications.router)
api_router.include_router(company.router)
api_router.include_router(invitations.router)