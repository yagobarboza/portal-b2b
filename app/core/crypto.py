"""Cifragem simétrica para credenciais de integração (seção 42).

Usa Fernet (AES-128-CBC + HMAC) com chave derivada de SECRET_KEY.
- A chave é derivada por SHA-256 de SECRET_KEY (ou INTEGRATION_ENCRYPTION_KEY,
  se definida, para permitir rotação independente).
- Valores cifrados são prefixados com "enc:" para distinguir de texto puro.
- NUNCA armazenar credenciais do cliente em claro.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_PREFIX = "enc:"

def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def _current_fernet() -> Fernet:
    s = get_settings()
    return _fernet(s.INTEGRATION_ENCRYPTION_KEY or s.SECRET_KEY)

def encrypt_str(plain: str) -> str:
    """Cifra um valor. Retorna 'enc:<token>'."""
    if plain is None:
        return ""
    return _PREFIX + _current_fernet().encrypt(str(plain).encode("utf-8")).decode("utf-8")

def decrypt_str(cipher: str) -> str:
    """Decifra um valor 'enc:<token>'. Se não estiver cifrado, devolve como está."""
    if not cipher or not cipher.startswith(_PREFIX):
        return cipher
    token = cipher[len(_PREFIX):].encode("utf-8")
    try:
        return _current_fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        # Compatibilidade de leitura para credenciais cifradas antes da chave
        # dedicada. A próxima gravação já usa INTEGRATION_ENCRYPTION_KEY.
        settings = get_settings()
        if not settings.INTEGRATION_ENCRYPTION_KEY:
            raise
        return _fernet(settings.SECRET_KEY).decrypt(token).decode("utf-8")
