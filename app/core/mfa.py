"""MFA via TOTP (seção 11 do doc).

- Gera secret TOTP e QR code para o app autenticador.
- Verifica códigos TOTP.
- Gera códigos de recuperação (uso único) e os armazena como HASH.
"""
import base64
import hashlib
import io
import secrets

import pyotp
import qrcode

from app.core.config import get_settings

settings = get_settings()

def generate_secret() -> str:
    """Gera um secret TOTP novo (base32)."""
    return pyotp.random_base32()

def get_totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret)

def verify_totp(secret: str, code: str) -> bool:
    """Verifica o código TOTP com tolerância de 1 passo (clock drift)."""
    totp = get_totp(secret)
    return totp.verify(code, valid_window=1)

def provisioning_uri(secret: str, email: str) -> str:
    """URI para o app autenticador (otpauth://)."""
    return get_totp(secret).provisioning_uri(name=email, issuer_name=settings.MFA_ISSUER)

def qr_code_data_uri(secret: str, email: str) -> str:
    """QR code em data URI (para exibir no frontend)."""
    uri = provisioning_uri(secret, email)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"

def generate_recovery_codes(count: int = 8) -> list[str]:
    """Gera códigos de recuperação de uso único (seção 11)."""
    return [secrets.token_hex(4).upper() for _ in range(count)]

# ---------- Códigos de recuperação (armazenados como HASH) ----------

def hash_recovery_code(code: str) -> str:
    """Hash SHA-256 do código de recuperação (nunca guardar em texto puro).

    Os códigos são aleatórios de alta entropia (token_hex), então SHA-256
    sem salt é seguro e permite comparação determinística.
    """
    return hashlib.sha256(code.encode("utf-8")).hexdigest()

def hash_recovery_codes(codes: list[str]) -> list[str]:
    """Aplica hash em todos os códigos gerados (para armazenar no banco)."""
    return [hash_recovery_code(c) for c in codes]

def verify_recovery_code(code: str, hashed_codes: list[str]) -> bool:
    """Verifica se o código informado bate com algum hash armazenado."""
    return hash_recovery_code(code) in (hashed_codes or [])