# Contrato de execução em produção

Uma única imagem atende quatro processos, cada um implantado separadamente:

| Processo | Comando | Porta HTTP | Escala inicial |
|---|---|---:|---:|
| API | comando padrão da imagem | `${PORT}` (8080) | 1 ou mais |
| Consumidor ARQ | `python -m worker.main` | `WORKER_METRICS_PORT` | 1 ou mais |
| Scheduler ARQ | `python -m worker.scheduler` | `WORKER_METRICS_PORT` | exatamente 1 |
| Migração | `alembic upgrade head` | nenhuma | job sob demanda |

## Regras obrigatórias

- A API nunca executa Alembic durante o boot.
- `API_SCHEDULER_ENABLED=false` em produção.
- O scheduler usa a fila isolada `arq:scheduler`; o consumidor usa a fila ARQ padrão.
- Migração deve terminar com sucesso antes da nova revisão da API receber tráfego.
- API, worker e scheduler usam a mesma revisão de imagem.
- O scheduler deve permanecer com mínimo e máximo de uma instância.
- Worker e scheduler precisam de CPU durante toda a vida da instância.
- `/api/v1/health/live` é a sonda de processo da API.
- `/api/v1/health/ready` valida PostgreSQL e Redis. Storage aparece no diagnóstico,
  mas não remove a API do balanceador quando apenas o provedor de arquivos falha.
- Métricas dos processos de background são expostas em `/metrics`.

## Configuração

Use `.env.production.example` como inventário. Segredos não devem ser gravados no
arquivo nem na imagem: devem ser injetados pelo Secret Manager. A aplicação falha
no boot quando uma configuração insegura de produção é detectada, incluindo
cookies sem HTTPS, CORS local/wildcard, chaves fracas, scheduler dentro da API ou
URLs de PostgreSQL/Redis ausentes.

Para Redis com TLS, monte a CA como arquivo e configure:

```text
REDIS_URL=rediss://USUARIO:SENHA@HOST:PORT/0
REDIS_SSL_CA_CERTS=/var/run/secrets/redis/ca.pem
REDIS_SSL_CHECK_HOSTNAME=true
```

## Ordem de release

1. Construir e publicar a imagem imutável.
2. Atualizar e executar o migration job com essa imagem.
3. Atualizar o worker consumidor.
4. Atualizar o scheduler singleton.
5. Atualizar a API.
6. Validar live, ready, métricas e uma execução de integração.

Se a migração falhar, a API antiga permanece atendendo e a nova revisão não deve
ser promovida.
