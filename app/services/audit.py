"""Serviço central de auditoria (seção 44 do doc).

Registra eventos de segurança e negócio no AuditLog.
Regras:
- NUNCA registrar senhas, tokens, secrets ou dados sensíveis (seção 43).
- O tenant_id e user_id vêm do TenantContext (rastreabilidade).
- Eventos de segurança: login, logout, login_failed, password_change,
  mfa_change, permission_change.
- Eventos de negócio: create, update, delete (por entidade).
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TenantContext
from app.core.logging import get_logger
from app.models import AuditLog

logger = get_logger("audit")

async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    entity: str,
    entity_id: Any = None,
    old_values: dict | None = None,
    new_values: dict | None = None,
    user_id: Any = None,
    tenant_id: Any = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Registra um evento de auditoria.

    IMPORTANTE: old_values/new_values NUNCA devem conter senhas,
    tokens, secrets ou dados financeiros sensíveis. Use apenas
    campos não sensíveis (ex.: status, nome, e-mail).
    """
    # Valores padrão vindos do contexto corrente
    if user_id is None:
        user_id = TenantContext.user_id()
    if tenant_id is None:
        tenant_id = TenantContext.tenant_id()

    entry = AuditLog(
        user_id=user_id,
        tenant_id=tenant_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        old_values=old_values,
        new_values=new_values,
        ip=ip,
        user_agent=user_agent,
    )
    session.add(entry)
    # flush para persistir junto com a transação corrente
    await session.flush()

    # Log estruturado do evento (sem dados sensíveis)
    logger.info(
        "audit_event",
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id else None,
        user_id=str(user_id) if user_id else None,
        tenant_id=str(tenant_id) if tenant_id else None,
    )