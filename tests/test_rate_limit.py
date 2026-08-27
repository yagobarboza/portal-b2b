"""Teste do rate limit do by-domain (Redis, Bloco 17/18) — httpx + API ao vivo."""
import uuid

async def test_by_domain_rate_limit_blocks_after_limit(client_anon):
    domain = f"rl-{uuid.uuid4().hex[:8]}.com.br"  # domínio único por execução
    statuses = []
    for _ in range(31):
        r = await client_anon.get(f"/companies/by-domain/{domain}")
        statuses.append(r.status_code)
    assert 404 in statuses        # domínio não existe → 404 nas primeiras
    assert statuses[-1] == 429    # 31ª requisição bloqueada (limite 30/60s)