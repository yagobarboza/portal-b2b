from fastapi import APIRouter

from app.api.v1.endpoints import auth, cart, catalog, files, health, lgpd, orders

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(lgpd.router)
api_router.include_router(catalog.router)
api_router.include_router(files.router)
api_router.include_router(cart.router)
api_router.include_router(orders.router)