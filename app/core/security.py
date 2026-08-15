"""Segurança de credenciais: hashing Argon2id e política de senha.

Seções 9 (senhas) e 42 (secrets) do documento.
"""
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings
from app.core.exceptions import ValidationFailedError

settings = get_settings()

# Argon2id com parâmetros seguros (OWASP recomendação)
_hasher = PasswordHasher(
    time_cost=3,        # iterações
    memory_cost=65536,  # 64 MiB
    parallelism=4,      # threads
    hash_len=32,
    salt_len=16,
)

def hash_password(password: str) -> str:
    """Gera o hash Argon2id com salt individual (seção 9)."""
    return _hasher.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    """Verifica a senha contra o hash. Nunca revela qual parte falhou."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False

def validate_password_strength(password: str) -> None:
    """Aplica a política de senha (seção 9). Mensagem genérica, sem detalhar."""
    s = settings
    errors: list[str] = []

    if len(password) < s.PASSWORD_MIN_LENGTH:
        errors.append(f"mínimo de {s.PASSWORD_MIN_LENGTH} caracteres")
    if s.PASSWORD_REQUIRE_UPPER and not re.search(r"[A-Z]", password):
        errors.append("letra maiúscula")
    if s.PASSWORD_REQUIRE_LOWER and not re.search(r"[a-z]", password):
        errors.append("letra minúscula")
    if s.PASSWORD_REQUIRE_DIGIT and not re.search(r"\d", password):
        errors.append("número")
    if s.PASSWORD_REQUIRE_SPECIAL and not re.search(r"[^A-Za-z0-9]", password):
        errors.append("caractere especial")

    if errors:
        raise ValidationFailedError(
            "A senha não atende à política de segurança."
        )