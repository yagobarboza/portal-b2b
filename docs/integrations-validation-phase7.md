# FASE 7 — Testes e validação

Esta matriz liga cada risco obrigatório a uma regressão executável. Os testes
de banco usam tenants descartáveis e PostgreSQL real; não usam SQLite nem mocks
para concorrência, constraints ou idempotência.

| Cenário obrigatório | Cobertura |
| --- | --- |
| Produto existente atualizado | `tests_phase7/test_database_regressions.py::test_product_upsert_updates_creates_deduplicates_and_isolates_tenants` |
| Produto novo criado | mesmo teste de upsert |
| Mesmo lote duas vezes sem duplicar | teste de upsert e `test_transactional_idempotency_and_failure_after_claim` |
| Mesmo SKU em tenants diferentes | teste de upsert multi-tenant |
| Duplicatas no mesmo lote | teste de upsert, último registro canônico vence |
| Duas atualizações concorrentes em ordem inversa | `test_stock_clock_rejects_inverse_concurrent_and_late_events` |
| Evento antigo após o novo | mesmo teste de relógio de estoque |
| ERP indisponível, timeout, 429 e 500 | `tests_phase7/test_security_and_contracts.py::test_erp_failures_are_retryable` |
| Credencial expirada | `test_expired_previous_webhook_credential_is_rejected` |
| Payload inválido sem corrupção | `test_null_zero_positive_and_invalid_payload_preserve_existing_data` |
| Falha após idempotency claim | `test_transactional_idempotency_and_failure_after_claim` |
| Webhook duplicado com ACK seguro | `tests_phase2/test_integration_security.py::test_webhook_duplicate_returns_safe_ack_without_enqueue` |
| Usuário sem permissão | `test_user_without_permission_cannot_manage_integrations` |
| SSRF loopback, privado, link-local e redirect | `test_ssrf_blocks_insecure_loopback_private_and_link_local` e `test_rest_connector_refuses_redirect` |
| Dois cards de arquivo independentes | frontend: `IntegrationFilePicker.test.tsx` |
| Preservação e rotação de segredos | `test_secret_modes_preserve_replace_and_clear` e `test_secret_rotation_keeps_only_the_previous_generation` |
| Estoque null, zero e positivo | `test_null_zero_positive_and_invalid_payload_preserve_existing_data` |
| Reconciliação encontra divergência | `test_reconciliation_detects_and_repairs_stock_divergence` |
| Contrato de cada adapter | `test_every_registered_adapter_honors_its_canonical_contract` |
| E2E dos quatro canais | `test_end_to_end_all_four_current_channels` (agent, file, webhook e API) |

## Comandos de validação

Backend:

```text
python -m pytest tests_phase2 tests_phase3 tests_phase4 tests_phase5 tests_phase6 tests_phase7 -q
```

Frontend:

```text
npm test
npm run typecheck
npm run build
```
