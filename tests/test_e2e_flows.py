"""Testes E2E (seção 50) — fluxos completos contra a API viva.

Fluxo: login -> catálogo -> abrir ticket -> mensagem -> status -> notificação.
"""
from tests.conftest import create_ticket

async def test_fluxo_login_catalogo_ticket(client_cliente, client_a):
    # 1) Catálogo visível ao cliente
    r = await client_cliente.get("/catalog/products?page_size=5")
    assert r.status_code == 200, f"Catálogo falhou: {r.status_code}"

    # 2) Cliente abre ticket
    ticket_id = await create_ticket(client_cliente, "Teste E2E", "Fluxo completo do Bloco 15")

    # 3) Cliente envia mensagem
    r = await client_cliente.post(
        f"/tickets/{ticket_id}/messages",
        json={"content": "Mensagem de teste E2E"},
    )
    assert r.status_code == 201, f"Mensagem falhou: {r.status_code} {r.text}"

    # 4) Atendente muda o status (TicketStatusUpdate: status + note opcional)
    r = await client_a.patch(
        f"/tickets/{ticket_id}/status",
        json={"status": "under_review", "note": "Em análise pelo suporte"},
    )
    assert r.status_code == 200, f"Status falhou: {r.status_code} {r.text}"

    # 5) Cliente vê o ticket atualizado + notificação
    r = await client_cliente.get(f"/tickets/{ticket_id}")
    assert r.status_code == 200
    r = await client_cliente.get("/notifications")
    assert r.status_code == 200