"""Cifragem simétrica para credenciais de integração (seção 42).

Usa Fernet (AES-128-CBC + HMAC) com chave derivada de SECRET_KEY.
- A chave é derivada por SHA-256 de SECRET_KEY (ou INTEGRATION_ENCRYPTION_KEY,
  se definida, para permitir rotação independente).
- Valores cifrados são prefixados com "enc:" para distinguir de texto puro.
- NUNCA armazenar credenciais do cliente em claro (config_encrypted).
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import get_settings

_PREFIX = "enc:"

def _fernet() -> Fernet:
    s = get_settings()
    secret = getattr(s, "INTEGRATION_ENCRYPTION_KEY", "") or s.SECRET_KEY
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def encrypt_str(plain: str) -> str:
    """Cifra um valor. Retorna 'enc:<token>'."""
    if plain is None:
        return ""
    return _PREFIX + _fernet().encrypt(str(plain).encode("utf-8")).decode("utf-8")

def decrypt_str(cipher: str) -> str:
    """Decifra um valor 'enc:<token>'. Se não estiver cifrado, devolve como está."""
    if not cipher or not cipher.startswith(_PREFIX):
        return cipher
    return _fernet().decrypt(cipher[len(_PREFIX):].encode("utf-8")).decode("utf-8")