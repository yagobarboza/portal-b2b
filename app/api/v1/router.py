from fastapi import APIRouter

from app.api.v1.endpoints import auth, catalog, health, lgpd

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(lgpd.router)
api_router.include_router(catalog.router)