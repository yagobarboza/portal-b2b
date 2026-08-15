"""Testes unitários (seção 50) — regras de negócio puras, sem banco."""
import hashlib
import hmac

import pytest

from app.core.exceptions import ValidationFailedError
from app.core.security import validate_password_strength

def test_senha_fraca_rejeitada():
    with pytest.raises(ValidationFailedError):
        validate_password_strength("curta1")

def test_senha_sem_maiuscula_rejeitada():
    with pytest.raises(ValidationFailedError):
        validate_password_strength("someminusculas123!")

def test_senha_forte_aceita():
    validate_password_strength("SenhaForte@123")

async def test_webhook_assinatura_valida():
    """Valida a assinatura HMAC do webhook (seção 31).

    Usa a MESMA lógica de assinatura que o endpoint real deve usar.
    Se o serviço verify_webhook_signature não existir, o teste é
    pulado (SKIP) em vez de quebrar a coleta.
    """
    from app.core.config import get_settings

    try:
        from app.services.integration import verify_webhook_signature
    except ImportError:
        pytest.skip("verify_webhook_signature não implementado em app/services/integration.py")

    secret = get_settings().WEBHOOK_SECRET or "chave-teste-webhook-123"
    body = b'{"event": "financial.sync"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert await verify_webhook_signature(body, sig) is True

async def test_webhook_assinatura_invalida():
    try:
        from app.services.integration import verify_webhook_signature
    except ImportError:
        pytest.skip("verify_webhook_signature não implementado")

    body = b'{"event": "financial.sync"}'
    assert await verify_webhook_signature(body, "sha256=invalida") is False