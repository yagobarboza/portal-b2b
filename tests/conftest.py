"""Fixtures compartilhadas — Bloco 15 (seção 50/52).

Clientes por TESTE (não por sessão): cada teste cria seu próprio
httpx.AsyncClient e faz login. Evita o erro "Event loop is closed"
causado por clientes de sessão presos a um loop que já fechou.
"""
import os

import httpx
import pytest

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000/api/v1")

CREDENTIALS = {
    "agente_a": ("agente@teste.com", "Agente@12345"),
    "cliente_a": ("cliente2@teste.com", "Cliente@12345"),
    "usuario_b": ("usuarioB@teste.com", "UsuarioB@12345"),
}

async def login(client: httpx.AsyncClient, role: str) -> httpx.Response:
    email, password = CREDENTIALS[role]
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login {role} falhou: {r.status_code} {r.text}"
    return r

async def create_ticket(client: httpx.AsyncClient, title: str, description: str = "") -> str:
    """Cria um ticket e devolve o id (alinhado ao TicketCreate real)."""
    r = await client.post("/tickets", json={
        "title": title,
        "description": description,
        "priority": "low",
    })
    assert r.status_code == 201, f"Criação de ticket falhou: {r.status_code} {r.text}"
    return r.json()["id"]

async def _client(role: str) -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=BASE_URL)
    await login(c, role)
    return c

@pytest.fixture
async def client_a():
    c = await _client("agente_a")
    yield c
    await c.aclose()

@pytest.fixture
async def client_cliente():
    c = await _client("cliente_a")
    yield c
    await c.aclose()

@pytest.fixture
async def client_b():
    c = await _client("usuario_b")
    yield c
    await c.aclose()

@pytest.fixture
async def client_anon():
    async with httpx.AsyncClient(base_url=BASE_URL) as c:
        yield c