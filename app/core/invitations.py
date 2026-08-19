"""Utilitários de convites: token único, hash e expiração.

O token é um valor aleatório (secrets.token_urlsafe) enviado por e-mail.
No banco guardamos apenas o hash SHA-256 — se o banco vazar,
os tokens não podem ser reutilizados.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings

def generate_invite_token() -> str:
    """Token aleatório de uso único enviado ao convidado por e-mail."""
    return secrets.token_urlsafe(32)

def hash_invite_token(token: str) -> str:
    """Hash SHA-256 do token (é isso que fica no banco)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def compute_expires_at() -> datetime:
    """Data de expiração do convite (padrão: 72h via settings)."""
    settings = get_settings()
    return datetime.now(timezone.utc) + timedelta(
        hours=settings.INVITE_TOKEN_EXPIRE_HOURS
    )