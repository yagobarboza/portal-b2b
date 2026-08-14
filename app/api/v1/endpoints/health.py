from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.database.session import get_db

logger = get_logger("health")

router = APIRouter(tags=["health"])

@router.get("/health/live")
async def liveness() -> dict:
    """A aplicação está viva? (não verifica dependências)."""
    return {"status": "ok"}

async def _check_postgres(db: AsyncSession) -> str:
    try:
        await db.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001
        logger.exception("health_check_postgres_failed")
        return "error"

async def _check_redis(redis: Redis) -> str:
    try:
        await redis.ping()
        return "ok"
    except Exception:  # noqa: BLE001
        logger.exception("health_check_redis_failed")
        return "error"

async def _check_storage() -> str:
    """Cloudflare R2 (seção 47). Sem R2 configurado -> 'not_configured'.

    'not_configured' não derruba o readiness (dev); se configurado e
    falhar -> 'error' (dependência indisponível de verdade).
    """
    settings = get_settings()
    bucket = getattr(settings, "R2_BUCKET", "") or getattr(settings, "R2_BUCKET_NAME", "")
    endpoint = getattr(settings, "R2_ENDPOINT", "") or getattr(settings, "R2_ENDPOINT_URL", "")
    if not (bucket and endpoint):
        return "not_configured"
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=getattr(settings, "R2_ACCESS_KEY_ID", ""),
            aws_secret_access_key=getattr(settings, "R2_SECRET_ACCESS_KEY", ""),
        )
        s3.head_bucket(Bucket=bucket)
        return "ok"
    except Exception:  # noqa: BLE001
        logger.exception("health_check_storage_failed")
        return "error"

@router.get("/health/ready")
async def readiness(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    """A aplicação está pronta para receber tráfego?

    Diferencia "app de pé" de "dependência indisponível" (seção 47).
    """
    checks: dict[str, str] = {
        "postgres": await _check_postgres(db),
        "redis": await _check_redis(redis),
        "storage": await _check_storage(),
    }

    # Dependências críticas: postgres e redis. storage 'not_configured' não falha.
    critical = {k: v for k, v in checks.items() if k != "storage"}
    healthy = all(status == "ok" for status in critical.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "checks": checks,
        },
    )