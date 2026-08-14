from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.core.logging import get_logger
from app.database.session import get_db

logger = get_logger("health")

router = APIRouter(tags=["health"])

@router.get("/health/live")
async def liveness() -> dict:
    """A aplicação está viva? (não verifica dependências)."""
    return {"status": "ok"}

@router.get("/health/ready")
async def readiness(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    """A aplicação está pronta para receber tráfego?

    Diferencia "app de pé" de "dependência indisponível" (seção 47 do doc).
    """
    checks: dict[str, str] = {}

    # PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        logger.exception("health_check_postgres_failed")
        checks["postgres"] = "error"

    # Redis
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        logger.exception("health_check_redis_failed")
        checks["redis"] = "error"

    healthy = all(status == "ok" for status in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "checks": checks,
        },
    )