"""Autenticação (seções 8/52/66).

- Sem token -> sem acesso (nunca 200).
- Senha errada / e-mail inexistente -> 401 com mensagem genérica (sem enumeração).
- Brute force: teste opcional (desativado por padrão — bloqueia o IP por 1 min).
"""
import os

import pytest

BRUTE_FORCE = os.getenv("TEST_BRUTE_FORCE", "") == "1"

async def test_sem_token_sem_acesso(client_anon):
    """Sem autenticação, nenhuma rota protegida pode retornar 200.

    401 = exige auth | 404 = rota não existe (também não vaza).
    """
    for path in ("/notifications", "/tickets", "/integrations",
                 "/orders", "/files", "/catalog/products"):
        r = await client_anon.get(path)
        assert r.status_code != 200, f"{path} sem token retornou 200!"
        assert r.status_code != 500, f"{path} sem token retornou 500!"

async def test_health_publico(client_anon):
    """Health check é público por design (não exige auth)."""
    r = await client_anon.get("/health/live")
    assert r.status_code == 200

async def test_senha_errada_mensagem_generica(client_anon):
    r = await client_anon.post("/auth/login", json={
        "email": "agente@teste.com", "password": "SenhaErrada123!",
    })
    assert r.status_code in (401, 403)
    text = r.text.lower()
    assert "não existe" not in text, "Enumeração de conta (senha errada)!"

async def test_email_inexistente_mensagem_generica(client_anon):
    r = await client_anon.post("/auth/login", json={
        "email": "nao-existe@teste.com", "password": "Qualquer123!",
    })
    assert r.status_code in (401, 403)
    text = r.text.lower()
    assert "não existe" not in text, "Enumeração de conta (e-mail inexistente)!"

@pytest.mark.skipif(not BRUTE_FORCE, reason="Ative com TEST_BRUTE_FORCE=1")
async def test_brute_force_rate_limit(client_anon):
    """Hammer no login até o rate limit disparar (seção 8).

    ATENÇÃO: bloqueia o IP por ~1 min — rode isolado, por último:
    docker compose exec -e TEST_BRUTE_FORCE=1 api pytest tests/test_auth.py -v
    """
    statuses = set()
    for _ in range(12):
        r = await client_anon.post("/auth/login", json={
            "email": "agente@teste.com", "password": "Errada123!",
        })
        statuses.add(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in statuses, "Rate limit de login não disparou"