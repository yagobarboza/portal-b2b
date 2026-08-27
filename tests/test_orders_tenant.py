"""Testes do fluxo de aprovação de pedidos do tenant — Bloco 18 (httpx + API ao vivo)."""
import uuid

UNIQUE = uuid.uuid4().hex[:8]

async def _create_product(client) -> str:
    r = await client.post("/catalog/products", json={
        "sku": f"SKU-{UNIQUE}",
        "name": f"Produto {UNIQUE}",
        "price": 10.0,
        "stock": 100,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]

async def _checkout_as_customer(client, product_id) -> str:
    r = await client.post("/cart/items", json={"product_id": product_id, "quantity": 2})
    assert r.status_code in (200, 201), r.text
    r = await client.post("/orders", json={"notes": "Teste Bloco 18"})
    assert r.status_code == 201, r.text
    return r.json()["id"]

async def test_tenant_lists_and_approves_order(client_a, client_cliente):
    pid = await _create_product(client_a)
    oid = await _checkout_as_customer(client_cliente, pid)

    r = await client_a.get("/orders/tenant")
    assert r.status_code == 200, r.text
    assert any(o["id"] == oid for o in r.json()["items"])

    r = await client_a.patch(
        f"/orders/{oid}/status",
        json={"status": "approved", "note": "Crédito aprovado"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert any(h["to_status"] == "approved" for h in body["status_history"])

async def test_customer_cannot_approve_order(client_a, client_cliente):
    pid = await _create_product(client_a)
    oid = await _checkout_as_customer(client_cliente, pid)
    r = await client_cliente.patch(f"/orders/{oid}/status", json={"status": "approved"})
    assert r.status_code in (403, 404)

async def test_tenant_b_cannot_approve_tenant_a_order(client_a, client_b, client_cliente):
    pid = await _create_product(client_a)
    oid = await _checkout_as_customer(client_cliente, pid)
    r = await client_b.patch(f"/orders/{oid}/status", json={"status": "approved"})
    assert r.status_code == 404  # cross-tenant