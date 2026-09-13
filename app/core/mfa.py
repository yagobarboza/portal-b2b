"""MFA via TOTP (seção 11 do doc).

- Gera secret TOTP e QR code para o app autenticador.
- Verifica códigos TOTP com janela de tempo ESTRITA (rejeita códigos antigos).
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

# ✅ Janela de tolerância MÍNIMA (RFC 6238).
# - TOTP_PERIOD: 30 segundos por passo (padrão do protocolo).
# - TOTP_WINDOW: 1 → aceita apenas o código do passo ATUAL e, no máximo,
#   1 passo anterior (30s) para compensar pequeno clock drift do dispositivo.
#   Códigos mais antigos (minutos/horas) são REJEITADOS.
TOTP_PERIOD = 30
TOTP_WINDOW = 1

def generate_secret() -> str:
    """Gera um secret TOTP novo (base32)."""
    return pyotp.random_base32()

def get_totp(secret: str) -> pyotp.TOTP:
    """Instância TOTP com período explícito (30s)."""
    return pyotp.TOTP(secret, interval=TOTP_PERIOD)

def _secret_is_valid(secret: str) -> bool:
    """True se o secret é não-vazio e base32 válido.

    ✅ FIX: sem esta validação, um secret vazio/inválido poderia fazer o
    pyotp aceitar códigos de forma indevida. Agora qualquer secret inválido
    é tratado como "código inválido" (retorna False).
    """
    if not secret:
        return False
    try:
        # base64.b32decode valida o formato; pyotp também lança em base32 inválido.
        base64.b32decode(secret.upper().replace(" ", ""))
        return True
    except Exception:
        return False

def verify_totp(secret: str, code: str) -> bool:
    """Verifica o código TOTP contra o relógio ATUAL com janela mínima.

    ✅ FIX (nenhum código aceito): o pyotp NÃO aceita o parâmetro `time`
    no verify — isso gerava TypeError e retornava False sempre. O correto
    é usar `valid_window` (o pyotp usa o relógio atual por padrão) ou o
    parâmetro `for_time`. Códigos antigos continuam REJEITADOS (janela=1).

    - Secret vazio/inválido → retorna False (nunca aceita).
    - Código do passo atual (0–30s) → aceito.
    - Código de até 30s atrás (clock drift) → aceito.
    - Código mais antigo → REJEITADO.
    """
    if not _secret_is_valid(secret) or not code:
        return False
    try:
        totp = get_totp(secret)
        return totp.verify(code, valid_window=TOTP_WINDOW)
    except Exception:
        # Nunca levanta: qualquer falha de validação vira "código inválido".
        return False

def verify_totp_at(secret: str, code: str, timestamp: float) -> bool:
    """Verifica o código TOTP em um instante específico (uso em testes).

    ✅ FIX: usa `for_time` (parâmetro correto do pyotp), não `time`.
    """
    if not _secret_is_valid(secret) or not code:
        return False
    try:
        totp = get_totp(secret)
        return totp.verify(code, valid_window=TOTP_WINDOW, for_time=timestamp)
    except Exception:
        return False

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