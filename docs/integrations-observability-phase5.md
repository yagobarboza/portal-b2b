# Fase 5 — Observabilidade de integrações

## Execuções e diagnóstico

`sync_executions` é o registro canônico de execução. Além do estado terminal, ele guarda origem (`trigger`), correlação, tentativas, duração, tamanho de entrada e contadores separados de itens recebidos, criados, atualizados, inalterados, obsoletos, ignorados e com erro.

Erros por item são limitados por `INTEGRATION_MAX_ITEM_ERRORS`. Somente `row`, `index`, `sku`, `code` e `error` são persistidos; registro bruto, credenciais, documentos e e-mails são removidos ou redigidos.

## Logs, métricas e Sentry

O worker emite eventos estruturados para início, conclusão, retry e falha terminal, sempre com IDs técnicos e contadores, nunca com payload ou configuração. O redator compartilhado protege logs e eventos do Sentry; corpos de request e identidade do usuário não são enviados ao Sentry.

A API publica métricas em `/metrics` e o worker em `WORKER_METRICS_PORT` (padrão `9100`):

- `integration_runs_total` e `integration_run_duration_seconds`;
- `integration_items_total`;
- `integration_retries_total`;
- `integration_queue_depth`;
- `integration_open_alerts`;
- `integration_last_success_timestamp_seconds`;
- `integration_payloads_purged_total`.

## Retenção

O job diário primeiro remove o conteúdo sensível e mantém metadados operacionais. Por padrão, payloads duram 30 dias, eventos/inbox/arquivos terminados 90 dias e o resumo de execução 365 dias. Os períodos são configuráveis pelas variáveis `INTEGRATION_*_RETENTION_DAYS`. Replay após expurgo é rejeitado explicitamente.

## Dashboard e alertas

`GET /api/v1/integrations/dashboard` exige `integrations:read`, respeita o tenant e nunca retorna payloads ou credenciais. Ele reúne último sucesso, última falha, duração média, fila, falhas consecutivas e alertas abertos.

O worker reavalia periodicamente dois alertas persistidos:

- `recurring_failure`, aberto após `INTEGRATION_FAILURE_ALERT_THRESHOLD` falhas consecutivas;
- `stale_success`, aberto quando não há sucesso há `INTEGRATION_STALE_SUCCESS_HOURS`.

Os alertas são resolvidos automaticamente quando a condição deixa de existir.
