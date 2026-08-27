"""Testes do image_url nos produtos — Bloco 18 (httpx + API ao vivo)."""
import uuid

UNIQUE = uuid.uuid4().hex[:8]

async def test_product_image_url_none_when_no_image(client_a):
    r = await client_a.post("/catalog/products", json={
        "sku": f"SKU-IMG-{UNIQUE}",
        "name": f"Produto {UNIQUE}",
        "price": 10.0,
        "stock": 5,
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r = await client_a.get(f"/catalog/products/{pid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "image_url" in body
    assert body["image_url"] is None