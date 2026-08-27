"""Testes de Equipe (users) e Perfis (roles) — Bloco 18 (httpx + API ao vivo)."""
import uuid

UNIQUE = uuid.uuid4().hex[:8]

async def _me(client) -> str:
    r = await client.get("/auth/me")
    assert r.status_code == 200, r.text
    return r.json()["id"]

async def test_list_users(client_a):
    r = await client_a.get("/users")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1

async def test_update_user_status(client_a):
    me = await _me(client_a)
    r = await client_a.get("/users")
    items = r.json()["items"]
    target = next((u for u in items if u["id"] != me), None)
    if target is None:
        return  # só existe o próprio usuário — nada a atualizar
    r = await client_a.patch(f"/users/{target['id']}", json={"status": "inactive"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive"

async def test_cannot_deactivate_self(client_a):
    me = await _me(client_a)
    r = await client_a.patch(f"/users/{me}", json={"status": "inactive"})
    assert r.status_code == 422  # não pode desativar a si mesmo
    

async def test_create_role(client_a):
    r = await client_a.post("/roles", json={
        "name": f"Gerente Regional {UNIQUE}",
        "slug": f"gerente-regional-{UNIQUE}",
        "description": "Teste",
        "permission_codes": ["orders:read", "customers:read"],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert "orders:read" in body["permissions"]

async def test_cannot_edit_system_role(client_a):
    r = await client_a.get("/roles")
    assert r.status_code == 200, r.text
    system = next((role for role in r.json()["items"] if role.get("is_system")), None)
    if system is None:
        return  # sem role de sistema exposta — nada a testar
    r = await client_a.patch(f"/roles/{system['id']}", json={"permission_codes": ["orders:read"]})
    assert r.status_code == 422

async def test_role_cross_tenant_idor(client_a, client_b):
    r = await client_a.post("/roles", json={
        "name": f"Role A {UNIQUE}",
        "slug": f"role-a-{UNIQUE}",
        "permission_codes": ["orders:read"],
    })
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = await client_b.patch(f"/roles/{rid}", json={"permission_codes": ["orders:read"]})
    assert r.status_code == 404  # cross-tenant