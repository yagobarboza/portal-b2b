"""Serviço de conformidade LGPD (seção 45 do doc).

Princípios implementados tecnicamente:
- minimização: coletar apenas o necessário (schemas já aplicam).
- finalidade: dados usados somente para a finalidade declarada.
- exclusão: direito ao esquecimento via anonimização (art. 18, VI).
- portabilidade: exportação dos dados do titular (art. 18, II).
- rastreabilidade: todas as operações registradas em auditoria.
- retenção: TTL no Redis e retenção configurável de logs.

Separação técnica x jurídica (seção 45): este módulo cobre os
requisitos técnicos. Obrigações jurídicas (DPO, registro ANPD,
política publicada) são responsabilidade da empresa.
"""
import secrets

from app.core.security import hash_password
from app.models import User

def anonymize_user(user: User) -> None:
    """Anonimiza os dados pessoais do titular (LGPD, art. 18, VI).

    - E-mail substituído por valor aleatório (bloqueia novos logins).
    - Nome removido.
    - Senha trocada por hash aleatório (login impossível).
    - MFA desativado e secret removido.
    """
    user.email = f"excluido-{secrets.token_hex(8)}@invalid.local"
    user.full_name = "Usuário excluído"
    user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.mfa_secret_encrypted = None
    user.mfa_enabled = False

def export_user_personal_data(user: User) -> dict:
    """Coleta os dados pessoais do titular para portabilidade (art. 18, II).

    Retorna apenas dados não sensíveis — nunca senha, tokens ou secrets.
    """
    return {
        "user_id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "is_super_admin": user.is_super_admin,
        "mfa_enabled": user.mfa_enabled,
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
    }