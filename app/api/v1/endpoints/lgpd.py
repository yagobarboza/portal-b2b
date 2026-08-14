"""Endpoints de LGPD (seção 45 do doc).

- POST /lgpd/export   → portabilidade (art. 18, II)
- POST /lgpd/deletion → direito ao esquecimento (art. 18, VI)
- GET  /lgpd/policy   → política de retenção (pública, sem dados sensíveis)
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.tokens import revoke_all_sessions
from app.database.session import get_db
from app.models import User
from app.services.audit import record_audit
from app.services.lgpd import anonymize_user, export_user_personal_data

router = APIRouter(prefix="/lgpd", tags=["LGPD"])
settings = get_settings()

def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None

@router.get("/policy")
async def lgpd_policy() -> dict:
    """Política de retenção do sistema (dados públicos, não sensíveis)."""
    return {
        "data_retention_days": settings.DATA_RETENTION_DAYS,
        "token_ttl_seconds": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "notes": "Logs de auditoria retidos conforme política; tokens expiram automaticamente.",
    }

@router.post("/export")
async def lgpd_export(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Exporta os dados pessoais do titular (portabilidade — LGPD art. 18, II)."""
    data = export_user_personal_data(user)
    await record_audit(
        db,
        action="lgpd_export",
        entity="user",
        entity_id=user.id,
        user_id=user.id,
        tenant_id=user.tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"data": data}

@router.post("/deletion")
async def lgpd_deletion(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Solicita exclusão dos dados (direito ao esquecimento — LGPD art. 18, VI).

    Anonimiza o titular, invalida todas as sessões e registra em auditoria.
    """
    user_id = user.id
    tenant_id = user.tenant_id
    anonymize_user(user)
    await revoke_all_sessions(user_id)
    await record_audit(
        db,
        action="lgpd_deletion",
        entity="user",
        entity_id=user_id,
        user_id=user_id,
        tenant_id=tenant_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"status": "ok", "message": "Seus dados foram anonimizados. Não é mais possível fazer login."}