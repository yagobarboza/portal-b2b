"""Geração e verificação de chaves de API para agentes de integração.

Regras (segurança):
- A chave é gerada aleatoriamente e NUNCA é persistida em claro.
- Persistimos apenas "PREFIXO:HASH_SHA256" — o prefixo serve para identificar
  a chave na UI (ex.: "pk_a1b2c3"), o hash para autenticar.
- A busca é por IGUALDADE EXATA: reconstruímos o registro a partir da chave
  apresentada, então não há `LIKE`/varredura de valores sensíveis.
"""
import hashlib
import secrets

# Tamanho do prefixo visível (identificação da chave).
KEY_PREFIX_LEN = 8
# Entropia da chave (bytes) — 32 bytes ≈ 43 caracteres base64-urlsafe.
KEY_BYTES = 32

def generate_api_key() -> str:
    """Gera uma nova chave de API (formato: 'pk_<token>')."""
    return f"pk_{secrets.token_urlsafe(KEY_BYTES)}"

def hash_api_key(raw: str) -> str:
    """SHA-256 hex da chave (determinístico — permite comparação direta)."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

def api_key_prefix(raw: str) -> str:
    """Prefixo visível da chave (nunca revela o segredo)."""
    return (raw or "")[:KEY_PREFIX_LEN]

def build_api_key_record(raw: str) -> str:
    """Registro persistível: 'PREFIXO:HASH' (sem a chave em claro)."""
    return f"{api_key_prefix(raw)}:{hash_api_key(raw)}"

def is_valid_record(value: str | None) -> bool:
    """True se o valor persistido está no formato 'PREFIXO:HASH'."""
    if not value or ":" not in value:
        return False
    prefix, _, digest = value.partition(":")
    return len(prefix) == KEY_PREFIX_LEN and len(digest) == 64

def record_prefix(value: str | None) -> str | None:
    """Extrai o prefixo de um registro persistido (ou None se inválido)."""
    if not is_valid_record(value):
        return None
    return value.partition(":")[0]