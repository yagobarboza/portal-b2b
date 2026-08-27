"""Testes da visão de carrinhos do tenant — Bloco 18 (httpx + API ao vivo)."""
import uuid

UNIQUE = uuid.uuid4().hex[:8]

async def test_tenant_lists_customer_carts(client_a, client_cliente):
    r = await client_a.get("/cart/tenant")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)

    # cliente adiciona item → tenant passa a ver o carrinho
    pr = await client_a.post("/catalog/products", json={
        "sku": f"SKU-C-{UNIQUE}",
        "name": f"Prod {UNIQUE}",
        "price": 5.0,
        "stock": 50,
    })
    assert pr.status_code == 201, pr.text
    pid = pr.json()["id"]
    r = await client_cliente.post("/cart/items", json={"product_id": pid, "quantity": 1})
    assert r.status_code in (200, 201), r.text

    r = await client_a.get("/cart/tenant")
    assert r.status_code == 200
    assert len(r.json()) >= 1

async def test_customer_cannot_access_tenant_carts(client_cliente):
    r = await client_cliente.get("/cart/tenant")
    assert r.status_code in (403, 404)