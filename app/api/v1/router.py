from fastapi import APIRouter

from app.api.v1.endpoints import auth, cart, catalog, chat, files, health, lgpd, orders, tickets

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