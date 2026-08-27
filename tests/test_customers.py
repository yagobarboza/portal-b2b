"""Testes do módulo de Clientes (Bloco 18) — estilo Bloco 15 (httpx + API ao vivo).

Requisito: API rodando em TEST_BASE_URL (default http://localhost:8000/api/v1).
"""
import io
import uuid

UNIQUE = uuid.uuid4().hex[:8]

async def _create_customer(client, name=None, email=None):
    r = await client.post("/customers", json={
        "name": name or f"Cliente {UNIQUE}",
        "email": email or f"cliente{UNIQUE}@teste.com",
        "phone": "11999999999",
        "document": f"{UNIQUE}0001",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]

async def test_list_customers(client_a):
    r = await client_a.get("/customers")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body

async def test_create_customer(client_a):
    r = await client_a.post("/customers", json={
        "name": f"Loja ABC {UNIQUE}",
        "email": f"loja{UNIQUE}@abc.com.br",
        "phone": "11999999999",
        "document": f"{UNIQUE}0001",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"].startswith("Loja ABC")
    assert body["status"] == "active"

async def test_create_customer_duplicate_email(client_a):
    email = f"dup{UNIQUE}@x.com.br"
    payload = {"name": "Loja A", "email": email}
    r1 = await client_a.post("/customers", json=payload)
    assert r1.status_code == 201, r1.text
    r2 = await client_a.post("/customers", json=payload)
    assert r2.status_code == 422  # VALIDATION_ERROR (e-mail duplicado)

async def test_customer_cannot_access_crm(client_cliente):
    """Cliente não tem customers:read — não acessa o CRM do tenant."""
    r = await client_cliente.get("/customers")
    assert r.status_code in (403, 404)

async def test_cross_tenant_isolation(client_a, client_b):
    """Tenant B não vê clientes criados pelo tenant A."""
    await _create_customer(client_a)
    r = await client_b.get("/customers")
    assert r.status_code == 200
    assert r.json()["total"] == 0

async def test_customer_idor(client_a, client_b):
    """IDOR: tenant B não acessa cliente do tenant A (404 genérico)."""
    cid = await _create_customer(client_a)
    r = await client_b.get(f"/customers/{cid}")
    assert r.status_code == 404

async def test_import_customers_csv(client_a):
    csv_content = (
        "name,email,phone,document\n"
        f"João,joao{UNIQUE}@x.com,119999,111\n"
        f"Maria,maria{UNIQUE}@x.com,118888,222\n"
    )
    r = await client_a.post(
        "/customers/import",
        files={"file": (f"clientes{UNIQUE}.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2
    assert body["skipped"] == 0
    assert body["errors"] == []

async def test_import_customers_csv_invalid_row(client_a):
    csv_content = "name,email\n,no-name@x.com\n"
    r = await client_a.post(
        "/customers/import",
        files={"file": ("c.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 0
    assert body["skipped"] == 1
    assert body["errors"][0]["error"] == "Nome é obrigatório"