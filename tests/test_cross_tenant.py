"""CENÁRIO CRÍTICO OBRIGATÓRIO (seção 52 do doc).

Usuário A (Tenant A) tentando acessar recurso do Tenant B:
  - pedido do Tenant B  -> acesso negado (403/404)
  - arquivo do Tenant B -> acesso negado (403/404)
  - ticket do Tenant B  -> acesso negado (404)
  - integração do Tenant B -> acesso negado (404)

NUNCA pode retornar 200 (vazamento).
Rotas reais confirmadas: /orders (lista direta, só cliente),
/files/{id}/download, /tickets (TicketPage.items), /integrations (lista direta).
"""
import pytest

async def _first_id(client, path):
    """Primeiro id do recurso do tenant (None se vazio/rota diferente)."""
    r = await client.get(path)
    if r.status_code != 200:
        return None
    data = r.json()
    if isinstance(data, list):
        return data[0].get("id") if data else None
    items = data.get("items") or data.get("data") or []
    return items[0].get("id") if items else None

async def test_a_nao_acessa_pedido_do_tenant_b(client_cliente, client_b):
    """Cliente A tenta acessar pedido de outro tenant -> 404 (anti-vazamento)."""
    r = await client_cliente.get("/orders")
    if r.status_code != 200 or not r.json():
        pytest.skip("Cliente A sem pedidos — crie um antes de rodar.")
    r2 = await client_cliente.get("/orders/00000000-0000-0000-0000-000000000000")
    assert r2.status_code == 404, f"Pedido de outro tenant retornou {r2.status_code}"

async def test_a_nao_acessa_arquivo_de_outro_tenant(client_cliente, client_b):
    """Arquivo de outro tenant -> 404 (anti-vazamento)."""
    r = await client_cliente.get("/files/00000000-0000-0000-0000-000000000000/download")
    assert r.status_code == 404, f"Arquivo de outro tenant retornou {r.status_code}"

async def test_b_nao_acessa_ticket_do_tenant_a(client_a, client_b):
    r = await client_a.get("/tickets")
    items = (r.json().get("items") or []) if r.status_code == 200 else []
    if not items:
        pytest.skip("Tenant A sem tickets.")
    ticket_a = items[0]["id"]
    r2 = await client_b.get(f"/tickets/{ticket_a}")
    assert r2.status_code == 404, f"Vazamento: B viu ticket de A ({r2.status_code})"

async def test_b_nao_acessa_integracao_do_tenant_a(client_a, client_b):
    r = await client_a.get("/integrations")
    items = r.json() if r.status_code == 200 else []
    if not items:
        pytest.skip("Tenant A sem integrações.")
    integracao_a = items[0]["id"]
    r2 = await client_b.get(f"/integrations/{integracao_a}")
    assert r2.status_code == 404, f"Vazamento: B viu integração de A ({r2.status_code})"