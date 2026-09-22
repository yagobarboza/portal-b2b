# Integrações — robustez, filas e concorrência (Fase 4)

## Garantias operacionais

- Webhook e agente são deduplicados pela chave única
  `(integration_id, channel, idempotency_key)` no PostgreSQL.
- O ACK do webhook acontece depois do commit da inbox e não depende do Redis.
- Uploads são persistidos com SHA-256 antes do enqueue e processados pelo worker.
- Dispatchers periódicos recuperam enqueue perdido e leases abandonadas de
  inbox, arquivos e execuções REST; locks de linha impedem execução duplicada.
- Estoque aceita `occurred_at` e `source_version`; evento antigo não sobrescreve
  saldo mais novo e uma versão repetida com valor diferente é conflito.
- Leituras e updates de estoque são divididos em chunks de 500 registros.
- Falhas HTTP `408`, `409`, `425`, `429` e `5xx`, timeouts e falhas de rede são
  transitórias. `Retry-After` prevalece sobre o backoff exponencial com jitter.
- Execuções permanentes terminam em `failed`; retries esgotados terminam em
  `dead_letter`. O replay cria outro registro e liga `replay_of_id`.

## Estados

Inbox: `pending -> processing -> succeeded`, ou `retry -> processing`; falha
permanente/esgotada vai para `dead_letter`.

Execução: `pending -> running -> success|partial`; falha transitória retorna a
`pending` com `next_retry_at`; falha permanente vai para `failed` e esgotamento
vai para `dead_letter`. Estados terminais recebem `terminal_at`.

## Scheduler e reconciliação

`integration_schedules` guarda intervalo, próximo vencimento, cursor, jitter e
limite. O cron reivindica no máximo 100 schedules com `FOR UPDATE SKIP LOCKED`,
avança `next_run_at` e cria a execução na mesma transação. Cada configuração
REST recebe:

- schedule de `stock` no intervalo configurado;
- schedule de `reconciliation` a cada 24 horas;
- cursor opcional via `cursor_param` e `next_cursor_path`.

A reconciliação é um snapshot periódico pelo mesmo connector/adapter e pelo
mesmo motor versionado de estoque; não há uma segunda implementação de update.

## Endpoints alterados/adicionados

- `POST /webhooks/{integration_id}`: `202`, com `inbox_id` e ACK idempotente.
- `POST /integrations/{id}/stock/import`: `202`, devolve `SyncExecutionRead`.
- `POST /integrations/{id}/api-config/pull`: `202`, enfileira com retry classificado.
- `POST /integrations/{id}/syncs/{sync_id}/replay`: replay de execução terminal.
- `POST /integrations/{id}/inbox/{inbox_id}/replay`: replay de inbox terminal.

Os arquivos permanecem armazenados para permitir replay. Retenção/expurgo pode
ser acrescentado como política operacional sem mudar o contrato do worker.
