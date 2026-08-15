# Escalabilidade — Portal B2B (seção 54)

## App stateless
- A API é stateless: autenticação via JWT em cookie (sem sessão em memória).
- Não há estado local no worker nem na API → pode escalar horizontalmente.

## Escala horizontal (múltiplas réplicas da API)
- Subir N réplicas atrás de um load balancer (nginx/ALB).
- Redis compartilhado: rate limit, cache de catálogo, idempotência de webhook.
- R2 (S3): armazenamento de arquivos centralizado.
- PostgreSQL: pool configurado (DB_POOL_SIZE/MAX_OVERFLOW) para N réplicas.
  - Regra: soma de (pool_size + max_overflow) x réplicas <= max_connections do Postgres.

## Cache (seção 53)
- Catálogo: Redis com TTL 60s + invalidação em escrita.
- Health checks: não cachear além do necessário (dependências mudam rápido).

## Recomendações de produção
- Postgres gerenciado (RDS/Cloud SQL) com réplica de leitura para relatórios.
- Redis gerenciado (ElastiCache/Upstash) com persistência.
- CDN (Cloudflare) na frente do R2 para arquivos públicos.
- Autoscaling da API por CPU/latência.